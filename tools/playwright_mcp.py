"""
Playwright MCP Bridge — 将现有 browser_test/browser_evaluate 接口映射到 Playwright MCP tools

使用 MCP (Model Context Protocol) 与 Playwright MCP Server 通信，替代内嵌的 sync_playwright。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config

log = logging.getLogger("harness")


def _wrap_script(script: str) -> str:
    """Wrap a raw JS expression into a serializable function for Playwright MCP.

    Playwright MCP's browser_evaluate tool requires the 'function' argument
    to be a complete, serializable JavaScript function. Many agents pass
    bare expressions like 'return document.title', which fail with
    'Passed function is not well-serializable!'.

    Rules:
    - Complete function declarations / arrow functions are passed through.
    - Scripts containing 'await' are wrapped in an async arrow function.
    - Scripts starting with 'return' keep the return statement.
    - Multi-line scripts are wrapped in braces.
    - Simple expressions get an automatic 'return'.
    """
    s = script.strip()

    # Already a complete function declaration → pass through
    if s.startswith("function ") or s.startswith("async function "):
        return s

    # Already a parenthesized arrow function expression → pass through
    # Covers: () => ..., (x) => ..., (x, y) => ...
    if s.startswith("("):
        return s

    # Already an async arrow function or async expression → pass through
    if s.startswith("async "):
        return s

    # Detect if script uses await (needs async wrapper)
    needs_async = "await " in s

    # Script starts with 'return' → wrap as-is (keep the return statement)
    if s.startswith("return "):
        if needs_async:
            return f"async () => {{ {s} }}"
        return f"() => {{ {s} }}"

    # Multi-line or complex script → wrap in braces without adding return
    if "\n" in s:
        if needs_async:
            return f"async () => {{ {s} }}"
        return f"() => {{ {s} }}"

    # Simple expression → wrap with implicit return
    if needs_async:
        return f"async () => {{ return {s}; }}"
    return f"() => {{ return {s}; }}"


class PlaywrightMCPBridge:
    """封装 Playwright MCP 调用，提供与现有 browser_test/browser_evaluate 兼容的接口。"""

    def __init__(self):
        self._session: ClientSession | None = None
        self._client_ctx = None
        self._read = None
        self._write = None

    async def _ensure_session(self) -> ClientSession:
        """确保 MCP session 已建立。"""
        if self._session is not None:
            return self._session

        params = StdioServerParameters(
            command="npx",
            args=[
                "--yes", "@playwright/mcp@latest",
                "--headless",
                "--browser", "chromium",
                "--console-level", "error",
            ],
        )
        self._client_ctx = stdio_client(params)
        try:
            self._read, self._write = await self._client_ctx.__aenter__()
            self._session = ClientSession(self._read, self._write)
            try:
                await self._session.__aenter__()
                await self._session.initialize()
                return self._session
            except Exception:
                # Session enter failed; clean up client context
                await self._client_ctx.__aexit__(*sys.exc_info())
                self._client_ctx = None
                self._session = None
                raise
        except Exception:
            # Client context enter failed; nothing to clean up yet
            self._client_ctx = None
            raise

    async def close(self) -> None:
        """关闭 MCP session 和浏览器进程。"""
        if self._session is not None:
            try:
                await self._session.call_tool("browser_close", {})
            except Exception:
                pass
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None

        if self._client_ctx is not None:
            try:
                # Use __aexit__ (symmetric with __aenter__) instead of aclose()
                # to avoid anyio CancelScope race during asyncio.run() shutdown.
                await self._client_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._client_ctx = None

    async def _call_tool(self, name: str, arguments: dict) -> str:
        """调用 MCP tool 并返回文本结果。"""
        session = await self._ensure_session()
        result = await session.call_tool(name, arguments)
        if result.content:
            return "\n".join(c.text for c in result.content if hasattr(c, "text"))
        return ""

    # ------------------------------------------------------------------ #
    #  browser_test 兼容接口                                             #
    # ------------------------------------------------------------------ #

    async def browser_test(
        self,
        url: str,
        actions: list | None = None,
        screenshot: bool = True,
        viewport: dict | None = None,
    ) -> str:
        """与现有 browser_test 接口兼容的实现。

        内部使用 Playwright MCP tools:
        - browser_navigate
        - browser_resize
        - browser_click / browser_type / browser_evaluate
        - browser_wait_for
        - browser_console_messages
        - browser_snapshot
        - browser_take_screenshot
        """
        report_lines: list[str] = []

        vp = viewport if isinstance(viewport, dict) else {"width": 1280, "height": 720}
        report_lines.append(f"Viewport: {vp['width']}x{vp['height']}")

        try:
            # 1. 设置 viewport
            await self._call_tool("browser_resize", {
                "width": vp["width"],
                "height": vp["height"],
            })

            # 2. 导航
            nav_result = await self._call_tool("browser_navigate", {"url": url})
            # 从 MCP 结果中提取标题
            title = self._extract_title(nav_result)
            report_lines.append(f"Navigated to {url} — title: {title}")

            # 3. 执行 actions
            for action in (actions or []):
                action_type = action.get("type", "")
                selector = action.get("selector", "")
                value = action.get("value", "")
                delay = action.get("delay", 1000)

                try:
                    if action_type == "click":
                        snapshot = await self._call_tool("browser_snapshot", {})
                        ref = self._find_ref_by_selector(snapshot, selector)
                        if ref:
                            await self._call_tool("browser_click", {"ref": ref})
                            report_lines.append(f"Clicked: {selector}")
                        else:
                            report_lines.append(f"[error] click: Element not found: {selector}")

                    elif action_type == "fill":
                        snapshot = await self._call_tool("browser_snapshot", {})
                        ref = self._find_ref_by_selector(snapshot, selector)
                        if ref:
                            await self._call_tool("browser_type", {"ref": ref, "text": value})
                            report_lines.append(f"Filled '{selector}'")
                        else:
                            report_lines.append(f"[error] fill: Element not found: {selector}")

                    elif action_type == "evaluate":
                        wrapped = _wrap_script(value)
                        result = await self._call_tool("browser_evaluate", {"function": wrapped})
                        # 提取结果部分
                        eval_text = self._extract_eval_result(result)
                        report_lines.append(f"JS eval: {eval_text[:500]}")

                    elif action_type == "wait":
                        await self._call_tool("browser_wait_for", {"time": delay / 1000})

                    elif action_type == "scroll":
                        scroll_amount = value if value else 500
                        await self._call_tool("browser_evaluate", {
                            "function": f"() => window.scrollBy(0, {scroll_amount})"
                        })

                except Exception as e:
                    report_lines.append(f"[error] {action_type}: {e}")

                # 每个 action 后短暂等待
                await asyncio.sleep(0.3)

            # 4. 获取当前 URL
            snapshot = await self._call_tool("browser_snapshot", {})
            current_url = self._extract_url(snapshot)
            report_lines.append(f"Final URL: {current_url}")

            # 5. 获取可见文本
            visible_text = self._extract_visible_text(snapshot)
            report_lines.append(f"Visible text: {visible_text[:2000]}")

            # 6. 控制台错误
            console_result = await self._call_tool("browser_console_messages", {"level": "error"})
            console_errors = self._parse_console_errors(console_result)
            if console_errors:
                report_lines.append(f"Console errors ({len(console_errors)}):")
                for err in console_errors[:10]:
                    report_lines.append(f"  - {err[:200]}")

            # 7. 截图
            if screenshot:
                ss_name = f"_screenshot_{vp['width']}x{vp['height']}.png"
                await self._call_tool("browser_take_screenshot", {"filename": str(ss_name)})
                report_lines.append(f"Screenshot saved to {ss_name}")

        except Exception as e:
            report_lines.append(f"[error] Browser test failed: {e}")

        return "\n".join(report_lines)

    # ------------------------------------------------------------------ #
    #  browser_evaluate 兼容接口                                         #
    # ------------------------------------------------------------------ #

    async def browser_evaluate(
        self,
        script: str,
        url: str | None = None,
        viewport: dict | None = None,
    ) -> str:
        """与现有 browser_evaluate 接口兼容的实现。"""
        try:
            vp = viewport if isinstance(viewport, dict) else {"width": 1280, "height": 720}

            # 设置 viewport
            await self._call_tool("browser_resize", {
                "width": vp["width"],
                "height": vp["height"],
            })

            # 导航（如果提供了 URL）
            if url:
                nav_result = await self._call_tool("browser_navigate", {"url": url})
                if "Error" in nav_result:
                    return f"[error] Navigation failed: {nav_result}"

            # 执行脚本 — Playwright MCP 要求 function 是完整可序列化的函数
            wrapped = _wrap_script(script)
            result = await self._call_tool("browser_evaluate", {"function": wrapped})
            eval_text = self._extract_eval_result(result)
            return f"Result: {eval_text}"

        except Exception as e:
            return f"[error] Script execution failed: {e}"

    # ------------------------------------------------------------------ #
    #  结果解析辅助方法                                                   #
    # ------------------------------------------------------------------ #

    def _extract_title(self, nav_result: str) -> str:
        """从 browser_navigate 结果中提取页面标题。"""
        for line in nav_result.splitlines():
            if "- Page Title:" in line:
                return line.split("- Page Title:", 1)[1].strip()
        return "N/A"

    def _extract_url(self, snapshot: str) -> str:
        """从 snapshot 结果中提取 URL。"""
        for line in snapshot.splitlines():
            if "- Page URL:" in line:
                return line.split("- Page URL:", 1)[1].strip()
        return "N/A"

    def _extract_visible_text(self, snapshot: str) -> str:
        """从 accessibility snapshot 中提取可见文本。"""
        lines = []
        for line in snapshot.splitlines():
            # 跳过 markdown 标题和元数据行
            if line.startswith("#") or line.startswith("-") or line.startswith("["):
                continue
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        return " ".join(lines)

    def _extract_eval_result(self, eval_result: str) -> str:
        """从 browser_evaluate 结果中提取返回值。"""
        # MCP 返回格式: ### Result\n"value"\n### Ran Playwright code...
        lines = eval_result.splitlines()
        in_result = False
        result_lines = []
        for line in lines:
            if line.strip() == "### Result":
                in_result = True
                continue
            if line.strip().startswith("### ") and in_result:
                break
            if in_result:
                result_lines.append(line)
        return "\n".join(result_lines).strip() or eval_result[:500]

    def _parse_console_errors(self, console_result: str) -> list[str]:
        """解析控制台错误消息。"""
        errors = []
        for line in console_result.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                errors.append(line)
        return errors

    def _find_ref_by_selector(self, snapshot: str, selector: str) -> str | None:
        """在 accessibility snapshot 中通过 selector 查找元素的 ref。

        解析 MCP browser_snapshot 返回的 markdown 格式，提取 ref 值。
        Snapshot 格式示例:
            - heading "Title" [ref=s1e2]
            - button "Click me" [ref=s1e3]
            - link "About" [ref=s1e4]
        """
        import re

        if not selector or not snapshot:
            return None

        # 提取 selector 中的关键文本（去除 CSS 前缀）
        search_text = selector.strip()
        # 去除常见的 CSS 前缀: #id, .class, [attr], tagname
        for prefix in ("#", ".", "[", "]"):
            search_text = search_text.replace(prefix, " ")
        search_text = search_text.strip().lower()

        if not search_text:
            return None

        # 解析 snapshot 中的每一行，查找 ref 和文本
        # 格式: - tag "text" [ref=xxx] 或 [ref=xxx] text
        ref_pattern = re.compile(r'\[ref=([^\]]+)\]')

        candidates = []
        for line in snapshot.splitlines():
            line_lower = line.lower()
            ref_match = ref_pattern.search(line)
            if not ref_match:
                continue

            ref = ref_match.group(1)
            # 移除 ref 部分，保留其余文本用于匹配
            text_without_ref = ref_pattern.sub('', line).lower()

            # 匹配策略：精确匹配 > 包含匹配 > 部分词匹配
            if search_text in text_without_ref:
                # 计算匹配质量：越短的行匹配越精确
                quality = len(text_without_ref) - len(search_text)
                candidates.append((quality, ref, line.strip()))

        if candidates:
            # 选择最精确的匹配（质量值最小）
            candidates.sort(key=lambda x: x[0])
            _, best_ref, matched_line = candidates[0]
            log.debug(f"[playwright_mcp] Selector '{selector}' matched ref={best_ref} in line: {matched_line[:100]}")
            return best_ref

        return None


# ---------------------------------------------------------------------- #
#  同步包装函数（供现有代码调用）                                         #
# ---------------------------------------------------------------------- #


def browser_test_mcp(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    viewport: dict | None = None,
) -> str:
    """同步包装：调用 Playwright MCP bridge 执行 browser_test。

    每次调用创建独立的 bridge 实例，并在同一个事件循环中完成和关闭，
    避免跨 asyncio.run() 复用 session 导致 async generator 状态污染。
    """
    async def _run() -> str:
        bridge = PlaywrightMCPBridge()
        try:
            return await bridge.browser_test(url, actions, screenshot, viewport)
        finally:
            await bridge.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        log.warning(f"[playwright_mcp] browser_test failed: {e}")
        return f"[error] Browser test failed: {e}"


def browser_evaluate_mcp(
    script: str,
    url: str | None = None,
    viewport: dict | None = None,
) -> str:
    """同步包装：调用 Playwright MCP bridge 执行 browser_evaluate。

    每次调用创建独立的 bridge 实例，避免跨事件循环复用。
    """
    async def _run() -> str:
        bridge = PlaywrightMCPBridge()
        try:
            return await bridge.browser_evaluate(script, url, viewport)
        finally:
            await bridge.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        log.warning(f"[playwright_mcp] browser_evaluate failed: {e}")
        return f"[error] browser_evaluate failed: {e}"


def close_mcp_bridge() -> None:
    """关闭 MCP bridge（兼容性保留，现为无操作）。

    Bridge 已在每个同步包装函数的内部 finally 块中关闭。
    """
    pass
