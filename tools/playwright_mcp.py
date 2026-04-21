"""
Playwright MCP Bridge — 将现有 browser_test/browser_evaluate 接口映射到 Playwright MCP tools

使用 MCP (Model Context Protocol) 与 Playwright MCP Server 通信，替代内嵌的 sync_playwright。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config

log = logging.getLogger("harness")


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
        self._read, self._write = await self._client_ctx.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self._session

    async def close(self) -> None:
        """关闭 MCP session 和浏览器。"""
        if self._session is not None:
            try:
                await self._session.call_tool("browser_close", {})
            except Exception:
                pass
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._client_ctx is not None:
            await self._client_ctx.__aexit__(None, None, None)
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
                        result = await self._call_tool("browser_evaluate", {"function": value})
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
                ss_path = Path(config.WORKSPACE) / ss_name
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

            # 执行脚本
            result = await self._call_tool("browser_evaluate", {"function": script})
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

        这是一个简化实现：尝试通过文本内容或属性匹配。
        更精确的匹配需要解析完整的 accessibility tree。
        """
        # 简单启发式：如果 selector 是 CSS 选择器，尝试从 snapshot 中找匹配文本
        # 实际使用中，MCP 的 snapshot 使用 ref 系统，这里做简化处理
        # TODO: 实现更精确的 selector -> ref 映射
        return None


# ---------------------------------------------------------------------- #
#  同步包装函数（供现有代码调用）                                         #
# ---------------------------------------------------------------------- #

_bridge: PlaywrightMCPBridge | None = None


def _get_bridge() -> PlaywrightMCPBridge:
    """获取或创建全局 bridge 实例。"""
    global _bridge
    if _bridge is None:
        _bridge = PlaywrightMCPBridge()
    return _bridge


def browser_test_mcp(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    viewport: dict | None = None,
) -> str:
    """同步包装：调用 Playwright MCP bridge 执行 browser_test。"""
    bridge = PlaywrightMCPBridge()
    try:
        result = asyncio.run(bridge.browser_test(url, actions, screenshot, viewport))
        return result
    except Exception as e:
        log.warning(f"[playwright_mcp] browser_test failed: {e}")
        return f"[error] Browser test failed: {e}"
    finally:
        try:
            asyncio.run(bridge.close())
        except Exception:
            pass


def browser_evaluate_mcp(
    script: str,
    url: str | None = None,
    viewport: dict | None = None,
) -> str:
    """同步包装：调用 Playwright MCP bridge 执行 browser_evaluate。"""
    bridge = PlaywrightMCPBridge()
    try:
        result = asyncio.run(bridge.browser_evaluate(script, url, viewport))
        return result
    except Exception as e:
        log.warning(f"[playwright_mcp] browser_evaluate failed: {e}")
        return f"[error] browser_evaluate failed: {e}"
    finally:
        try:
            asyncio.run(bridge.close())
        except Exception:
            pass


def close_mcp_bridge() -> None:
    """关闭 MCP bridge（清理资源）。"""
    global _bridge
    if _bridge is not None:
        try:
            asyncio.run(_bridge.close())
        except Exception as e:
            log.debug(f"[playwright_mcp] close failed: {e}")
        _bridge = None
