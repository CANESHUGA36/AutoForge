"""
Playwright MCP Bridge — Unified Browser Interaction Layer

Architecture:
  ┌─────────────────────────────────────────────┐
  │           BrowserSessionPool (Singleton)     │
  │  ┌─────────────┐  ┌─────────────┐          │
  │  │ Session #1  │  │ Session #2  │  ...     │
  │  │ (desktop)   │  │ (mobile)    │          │
  │  │ MCP Server  │  │ MCP Server  │          │
  │  │ + Chrome    │  │ + Chrome    │          │
  │  └─────────────┘  └─────────────┘          │
  └─────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────┐
  │           CacheManager                       │
  │  - Vite Server Cache (node_modules/.vite)   │
  │  - Vite Module Graph (.vite/)               │
  │  - Build Output (dist/)                     │
  │  - Browser HTTP Cache                       │
  │  - Service Worker Cache                     │
  └─────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────┐
  │           browser_check()                    │
  │  Single entry point, unified behavior        │
  └─────────────────────────────────────────────┘

Modes:
  - inspect:   DOM query, state check (script-based)
  - interact:  Click, type, scroll (action chain)
  - screenshot: Visual verification
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
import weakref
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config

log = logging.getLogger("harness")

# Python 3.11+ BaseExceptionGroup compatibility
try:
    _CLEANUP_EXC_TYPES: tuple = (Exception, BaseExceptionGroup)
except NameError:
    _CLEANUP_EXC_TYPES = (Exception,)


# ─────────────────────────────────────────────────────────────────────────────
# 1. CacheManager — 统一缓存处理
# ─────────────────────────────────────────────────────────────────────────────

class CacheManager:
    """管理所有与浏览器测试相关的缓存。"""

    @staticmethod
    def clear_vite_server_caches() -> None:
        """清除 Vite 服务端缓存（构建输出 + 模块图 + 预构建依赖）。"""
        ws = Path(config.WORKSPACE)
        cleared = []

        # 1. Vite 预构建依赖缓存
        for cache_dir in (ws / "node_modules" / ".vite", ws / ".vite"):
            if cache_dir.exists():
                try:
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    cleared.append(str(cache_dir))
                except Exception:
                    pass

        # 2. 生产构建输出
        dist_dir = ws / "dist"
        if dist_dir.exists():
            try:
                shutil.rmtree(dist_dir, ignore_errors=True)
                cleared.append("dist/")
            except Exception:
                pass

        # 3. Next.js 缓存（如果存在）
        for cache_dir in (ws / ".next" / "cache", ws / ".next" / "turbopack"):
            if cache_dir.exists():
                try:
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    cleared.append(str(cache_dir))
                except Exception:
                    pass

        # 4. 触发文件系统事件，强制 Vite 重新扫描
        # 修改文件内容再改回来，确保 Vite 的 HMR 检测到变化
        import time as _time
        for entry in (ws / "src" / "main.tsx", ws / "src" / "main.jsx",
                      ws / "src" / "index.tsx", ws / "src" / "App.tsx"):
            if entry.exists():
                try:
                    # 读取 -> 追加换行 -> 写回 -> 恢复原内容
                    # 这样文件 mtime 一定会变化，Vite 的 chokidar 会检测到
                    original = entry.read_text(encoding="utf-8")
                    entry.write_text(original + "\n", encoding="utf-8")
                    _time.sleep(0.1)
                    entry.write_text(original, encoding="utf-8")
                    cleared.append(f"hmr_trigger:{entry.name}")
                except Exception:
                    pass

        if cleared:
            log.info(f"[CacheManager] Cleared: {', '.join(cleared)}")

    @staticmethod
    async def clear_browser_caches(session: ClientSession) -> None:
        """通过 MCP 清除浏览器端缓存。"""
        try:
            # 清除 Service Worker / Cache API 缓存
            await session.call_tool("browser_evaluate", {
                "function": """() => {
                    try {
                        if ('caches' in window) {
                            caches.keys().then(ks => ks.forEach(k => caches.delete(k)));
                        }
                        // 清除 localStorage 中可能的 Vite HMR 状态
                        const viteKeys = Object.keys(localStorage).filter(k => k.includes('vite'));
                        viteKeys.forEach(k => localStorage.removeItem(k));
                        return 'browser_cache_cleared';
                    } catch(e) {
                        return 'clear_failed: ' + e.message;
                    }
                }"""
            })
        except Exception as e:
            log.debug(f"[CacheManager] Browser cache clear skipped: {e}")

    @staticmethod
    def add_cache_buster(url: str) -> str:
        """为 URL 添加时间戳参数，防止 HTTP 缓存。"""
        timestamp = int(time.time())
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}_t={timestamp}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. BrowserSession — 单个浏览器会话封装
# ─────────────────────────────────────────────────────────────────────────────

class BrowserSession:
    """封装单个 Playwright MCP Session（包含 MCP Server + Chrome + Page）。"""

    def __init__(self, viewport: dict | None = None):
        self.viewport = viewport or {"width": 1280, "height": 720}
        self._session: ClientSession | None = None
        self._client_ctx = None
        self._read = None
        self._write = None
        self._current_url: str | None = None
        self._initialized = False

    async def start(self) -> None:
        """启动 MCP Session 和浏览器。"""
        if self._initialized:
            return

        # FIX: Use system-installed chromium instead of letting MCP download
        # its own chrome-for-testing (which fails in network-restricted environments).
        chromium_path = "/root/.cache/ms-playwright/chromium-1219/chrome-linux64/chrome"
        args = [
            "--yes", "@playwright/mcp@latest",
            "--headless",
            "--browser", "chromium",
            "--console-level", "error",
        ]
        if Path(chromium_path).exists():
            args.extend(["--executable-path", chromium_path])
            log.info(f"[BrowserSession] Using system chromium: {chromium_path}")
        else:
            log.warning(f"[BrowserSession] System chromium not found at {chromium_path}, MCP will attempt to download")

        params = StdioServerParameters(
            command="npx",
            args=args,
        )

        self._client_ctx = stdio_client(params)
        try:
            self._read, self._write = await self._client_ctx.__aenter__()
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
            await self._session.initialize()

            # 设置 viewport
            await self._call_tool("browser_resize", {
                "width": self.viewport["width"],
                "height": self.viewport["height"],
            })

            self._initialized = True
            log.info(f"[BrowserSession] Started with viewport {self.viewport['width']}x{self.viewport['height']}")

        except Exception:
            await self._cleanup_on_error()
            raise

    async def close(self) -> None:
        """关闭浏览器和 MCP Session。"""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except _CLEANUP_EXC_TYPES:
                pass
            except Exception:
                pass
            finally:
                self._session = None

        if self._client_ctx is not None:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except _CLEANUP_EXC_TYPES:
                pass
            except Exception:
                pass
            finally:
                self._client_ctx = None

        self._initialized = False
        log.info("[BrowserSession] Closed")

    async def _cleanup_on_error(self) -> None:
        """初始化失败时的清理。"""
        if self._session is not None:
            try:
                await self._session.__aexit__(*sys.exc_info())
            except _CLEANUP_EXC_TYPES:
                pass
            except Exception:
                pass
            self._session = None

        if self._client_ctx is not None:
            try:
                await self._client_ctx.__aexit__(*sys.exc_info())
            except _CLEANUP_EXC_TYPES:
                pass
            except Exception:
                pass
            self._client_ctx = None

    async def _call_tool(self, name: str, arguments: dict) -> str:
        """调用 MCP tool。"""
        if not self._session:
            raise RuntimeError("BrowserSession not initialized")
        result = await self._session.call_tool(name, arguments)
        if result.content:
            return "\n".join(c.text for c in result.content if hasattr(c, "text"))
        return ""

    async def navigate(self, url: str, fresh: bool = False, wait: int = 2) -> dict:
        """导航到 URL，返回页面基本信息。"""
        # FIX: Convert file:// URLs to http://localhost for headless Chrome compatibility.
        # Headless Chrome has restrictions on file:// protocol that can cause empty pages.
        original_url = url
        if url.startswith("file://"):
            # Serve the file via a temporary local HTTP server
            url = self._serve_file_via_http(url)
            log.info(f"[BrowserSession] Converted {original_url} -> {url}")

        if fresh:
            url = CacheManager.add_cache_buster(url)

        nav_result = await self._call_tool("browser_navigate", {"url": url})
        self._current_url = original_url

        # 提取标题
        title = self._extract_title(nav_result)

        # 等待页面加载
        await asyncio.sleep(wait)

        # 如果是 fresh 模式，执行硬刷新确保拿到最新代码
        # Vite dev server 的内存缓存可能导致旧代码被 serve，即使磁盘文件已更新
        if fresh:
            # CRITICAL FIX: window.location.reload(true) only bypasses HTTP cache,
            # but does NOT clear the browser's compiled JavaScript module cache.
            # Vite's HMR keeps compiled modules in memory, so stale code persists.
            # We must close and recreate the entire browser session to get a truly clean state.
            log.info("[BrowserSession] Fresh mode: closing session for complete cache reset")
            await self.close()
            # SessionPool will auto-recreate on next get_session() call
            raise RuntimeError("SESSION_RESTART_REQUIRED")

        return {"url": original_url, "title": title}

    def _serve_file_via_http(self, file_url: str) -> str:
        """Start a minimal HTTP server to serve a local file.
        
        Headless Chrome has restrictions on file:// protocol.
        We serve the file via http://localhost instead.
        """
        import http.server
        import socketserver
        import threading
        from pathlib import Path
        
        # Extract file path from file:// URL
        file_path = file_url.replace("file://", "").replace("file:///", "/")
        file_path = Path(file_path).resolve()
        
        # Fallback: if the path doesn't exist and looks like a /workspace placeholder,
        # try mapping it to the actual config.WORKSPACE directory.
        if not file_path.exists():
            import config
            workspace = Path(config.WORKSPACE).resolve()
            # Check if the path is under /workspace (Linux) or a generic placeholder
            path_str = str(file_path).replace("\\", "/")
            if "/workspace" in path_str:
                # Extract the relative part after /workspace
                rel_idx = path_str.find("/workspace") + len("/workspace")
                relative = path_str[rel_idx:].lstrip("/")
                mapped = workspace / relative if relative else workspace / "index.html"
                if mapped.exists():
                    file_path = mapped
                else:
                    # Try workspace root as fallback
                    fallback = workspace / "index.html"
                    if fallback.exists():
                        file_path = fallback
        
        if not file_path.exists():
            log.warning(f"[BrowserSession] File not found: {file_path} (original URL: {file_url})")
            return file_url
        
        # Use workspace directory as root, or file's parent directory
        root_dir = str(file_path.parent)
        
        # Find an available port
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=root_dir, **kwargs)
            
            def log_message(self, format, *args):
                # Suppress HTTP server logs
                pass
        
        httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        
        # Store for cleanup
        if not hasattr(self, '_http_servers'):
            self._http_servers = []
        self._http_servers.append(httpd)
        
        # Return HTTP URL
        return f"http://127.0.0.1:{port}/{file_path.name}"

    async def execute_script(self, script: str) -> Any:
        """执行 JavaScript 并返回结果。"""
        wrapped = _wrap_script(script)
        result = await self._call_tool("browser_evaluate", {"function": wrapped})
        return self._extract_eval_result(result)

    async def take_screenshot(self, filename: str | None = None) -> str:
        """截图并返回文件路径。"""
        if not filename:
            vp = self.viewport
            filename = f"_screenshot_{vp['width']}x{vp['height']}.png"
        await self._call_tool("browser_take_screenshot", {"filename": filename})
        return filename

    async def get_snapshot(self) -> str:
        """获取页面可访问性快照。"""
        return await self._call_tool("browser_snapshot", {})

    async def get_console_errors(self) -> list[str]:
        """获取控制台错误。"""
        result = await self._call_tool("browser_console_messages", {"level": "error"})
        errors = []
        for line in result.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                errors.append(line)
        return errors

    # ── 辅助方法 ──

    def _extract_title(self, nav_result: str) -> str:
        for line in nav_result.splitlines():
            if "- Page Title:" in line:
                return line.split("- Page Title:", 1)[1].strip()
        return "N/A"

    def _extract_eval_result(self, eval_result: str) -> Any:
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
        text = "\n".join(result_lines).strip()

        # 尝试解析为 JSON
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return eval_result[:500]


# ─────────────────────────────────────────────────────────────────────────────
# 3. BrowserSessionPool — 会话池管理
# ─────────────────────────────────────────────────────────────────────────────

class BrowserSessionPool:
    """管理多个 BrowserSession，按 viewport 复用。
    
    ⚠️ 重要：由于 MCP stdio_client 不能跨 asyncio 事件循环复用，
    每次 browser_check 在新线程中运行时，Session 会被自动重建。
    同一线程/事件循环内的多次调用可以复用 Session。
    """

    _instance: BrowserSessionPool | None = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: dict[str, BrowserSession] = {}
            cls._instance._closed = False
        return cls._instance

    def _make_key(self, viewport: dict) -> str:
        return f"{viewport.get('width', 1280)}x{viewport.get('height', 720)}"

    async def get_session(self, viewport: dict | None = None) -> BrowserSession:
        """获取或创建指定 viewport 的 Session。"""
        vp = viewport or {"width": 1280, "height": 720}
        key = self._make_key(vp)

        # 检查现有 Session 是否仍然可用（同事件循环）
        if key in self._sessions:
            session = self._sessions[key]
            try:
                # 快速健康检查
                await session._session.call_tool("browser_evaluate", {
                    "function": "() => 'ping'"
                })
                return session
            except Exception:
                # Session 已损坏（跨事件循环或连接断开），移除并重建
                log.debug(f"[SessionPool] Session {key} stale, recreating")
                try:
                    await session.close()
                except Exception:
                    pass
                del self._sessions[key]

        # 创建新 Session
        session = BrowserSession(vp)
        await session.start()
        self._sessions[key] = session
        log.info(f"[SessionPool] Created session for {key}")
        return session

    async def close_all(self) -> None:
        """关闭所有 Session。"""
        for key, session in list(self._sessions.items()):
            try:
                await session.close()
            except _CLEANUP_EXC_TYPES:
                pass
            except Exception as e:
                log.warning(f"[SessionPool] Error closing session {key}: {e}")
        self._sessions.clear()
        self._closed = True
        BrowserSessionPool._instance = None
        log.info("[SessionPool] All sessions closed")

    async def invalidate_caches(self) -> None:
        """清除服务端缓存 + 所有浏览器端缓存。"""
        # 1. 服务端缓存
        CacheManager.clear_vite_server_caches()

        # 2. 浏览器端缓存
        for session in self._sessions.values():
            try:
                await CacheManager.clear_browser_caches(session._session)
            except Exception as e:
                log.debug(f"[SessionPool] Cache clear skipped: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. _wrap_script — 脚本包装（保持不变）
# ─────────────────────────────────────────────────────────────────────────────

def _wrap_script(script: str) -> str:
    """将原始 JS 表达式包装为 Playwright MCP 可序列化的函数。"""
    s = script.strip()

    if s.startswith("function ") or s.startswith("async function "):
        return s
    if s.startswith("("):
        return s
    if s.startswith("async "):
        return s

    needs_async = "await " in s

    if s.startswith("return "):
        if needs_async:
            return f"async () => {{ {s} }}"
        return f"() => {{ {s} }}"

    if "\n" in s:
        if needs_async:
            return f"async () => {{ {s} }}"
        return f"() => {{ {s} }}"

    _STATEMENT_KEYWORDS = (
        "var ", "let ", "const ", "function ", "if ", "for ", "while ",
        "try ", "switch ", "class ", "do ", "with ", "throw ",
    )
    if any(s.startswith(kw) for kw in _STATEMENT_KEYWORDS):
        if needs_async:
            return f"async () => {{ {s} }}"
        return f"() => {{ {s} }}"

    if needs_async:
        return f"async () => {{ return {s}; }}"
    return f"() => {{ return {s}; }}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. browser_check — 统一的浏览器交互入口
# ─────────────────────────────────────────────────────────────────────────────

async def _browser_check_async(
    url: str = "http://localhost:5173",
    mode: str = "inspect",
    viewport: dict | None = None,
    fresh: bool = False,
    wait: int = 2,
    actions: list | None = None,
    script: str | None = None,
    screenshot: bool = False,
    format: str = "json",
) -> dict | str:
    """
    统一的浏览器交互入口。

    Args:
        url: 目标 URL
        mode: "inspect" | "interact" | "screenshot"
        viewport: {"width": int, "height": int}
        fresh: True = 强制清除所有缓存并刷新
        wait: 页面加载后等待秒数
        actions: 交互动作列表（mode="interact" 时使用）
        script: JS 脚本（mode="inspect" 时使用）
        screenshot: 是否截图
        format: "json" | "text"

    Returns:
        结构化结果（dict）或文本报告（str）
    """
    pool = BrowserSessionPool()
    session = await pool.get_session(viewport)
    result: dict[str, Any] = {
        "mode": mode,
        "viewport": session.viewport,
        "timestamp": int(time.time()),
    }

    try:
        # ── Step 1: 缓存处理 ──
        if fresh:
            # FIX: Instead of closing the entire session (which is slow),
            # just clear browser caches and reload the page.
            # Only recreate session if viewport changed.
            await pool.invalidate_caches()
            wait = max(wait, 3)  # fresh 模式增加等待时间
            log.info(f"[browser_check] Fresh mode: caches cleared, wait={wait}s")

        # ── Step 2: 导航 ──
        try:
            nav_info = await session.navigate(url, fresh=fresh, wait=wait)
            result.update(nav_info)
        except RuntimeError as e:
            if "SESSION_RESTART_REQUIRED" in str(e):
                # Session was closed by navigate(fresh=True) for complete cache reset.
                # Get a brand new session from the pool and navigate again.
                log.info("[browser_check] Session restarted for fresh mode, re-navigating...")
                session = await pool.get_session(viewport)
                nav_info = await session.navigate(url, fresh=False, wait=wait)
                result.update(nav_info)
            else:
                raise

        # ── Step 3: 根据模式执行 ──
        if mode == "inspect":
            # 执行脚本并返回结果
            if script:
                try:
                    eval_result = await session.execute_script(script)
                    result["script_result"] = eval_result
                except Exception as e:
                    result["script_error"] = str(e)

        elif mode == "interact":
            # 执行 action 链
            action_results = []
            for action in (actions or []):
                action_type = action.get("type", "")
                action_result = {"type": action_type, "status": "ok"}

                try:
                    if action_type == "click":
                        snapshot = await session.get_snapshot()
                        ref = _find_ref_by_selector(snapshot, action.get("selector", ""))
                        if ref:
                            await session._call_tool("browser_click", {"ref": ref})
                        else:
                            action_result["status"] = "error"
                            action_result["error"] = f"Element not found: {action.get('selector')}"

                    elif action_type == "fill":
                        snapshot = await session.get_snapshot()
                        ref = _find_ref_by_selector(snapshot, action.get("selector", ""))
                        if ref:
                            await session._call_tool("browser_type", {
                                "ref": ref,
                                "text": action.get("value", "")
                            })
                        else:
                            action_result["status"] = "error"
                            action_result["error"] = f"Element not found: {action.get('selector')}"

                    elif action_type == "wait":
                        delay = action.get("delay", 1000) / 1000
                        await asyncio.sleep(delay)

                    elif action_type == "scroll":
                        amount = action.get("value", 500)
                        await session.execute_script(f"window.scrollBy(0, {amount})")

                    elif action_type == "evaluate":
                        eval_result = await session.execute_script(action.get("script", action.get("value", "")))
                        action_result["result"] = eval_result

                    elif action_type == "upload":
                        # Simulate file upload by setting files on a file input and dispatching change event
                        selector = action.get("selector", "")
                        files = action.get("files", [])
                        if not selector:
                            action_result["status"] = "error"
                            action_result["error"] = "upload action requires 'selector'"
                        elif not files:
                            action_result["status"] = "error"
                            action_result["error"] = "upload action requires 'files' array"
                        else:
                            # Build file objects and trigger change event via evaluate
                            file_objects = []
                            for f in files:
                                name = f.get("name", "file")
                                mime = f.get("type", "application/octet-stream")
                                content = f.get("content", "")
                                file_objects.append(f"new File(['{content}'], '{name}', {{ type: '{mime}' }})")
                            files_js = ", ".join(file_objects)
                            script = f"""
                            (() => {{
                                const input = document.querySelector('{selector}');
                                if (!input) return {{ error: 'File input not found: {selector}' }};
                                const dt = new DataTransfer();
                                [{files_js}].forEach(file => dt.items.add(file));
                                input.files = dt.files;
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                return {{ uploaded: true, fileCount: input.files.length }};
                            }})()
                            """
                            eval_result = await session.execute_script(script)
                            action_result["result"] = eval_result

                except Exception as e:
                    action_result["status"] = "error"
                    action_result["error"] = str(e)

                action_results.append(action_result)
                await asyncio.sleep(0.3)

            result["actions"] = action_results

        elif mode == "screenshot":
            screenshot_file = await session.take_screenshot()
            result["screenshot"] = screenshot_file

        # ── Step 4: 获取控制台错误 ──
        result["console_errors"] = await session.get_console_errors()

        # ── Step 5: 截图（如果请求）──
        if screenshot and mode != "screenshot":
            screenshot_file = await session.take_screenshot()
            result["screenshot"] = screenshot_file

        # ── Step 6: 获取页面快照摘要 ──
        try:
            snapshot = await session.get_snapshot()
            result["page_summary"] = _summarize_snapshot(snapshot)
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)
        log.error(f"[browser_check] Error: {e}")

    # 返回格式
    if format == "text":
        return _format_result_as_text(result)
    return result


def _run_async_in_thread(coro) -> Any:
    """在独立线程中运行 async coroutine，并抑制 MCP cleanup 的 stderr 噪音。"""
    import concurrent.futures
    import io
    import sys

    # 捕获 stderr 以过滤 MCP 的 cleanup race 噪音
    old_stderr = sys.stderr
    captured = io.StringIO()
    sys.stderr = captured

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(coro))
            try:
                return future.result(timeout=60)
            except _CLEANUP_EXC_TYPES as e:
                log.debug(f"[browser_check] suppressed cleanup race: {e}")
                return {"error": "MCP connection cleanup race"}
    finally:
        sys.stderr = old_stderr
        stderr_output = captured.getvalue()
        # 只输出非 MCP cleanup 的错误
        if stderr_output and "stdio_client" not in stderr_output:
            print(stderr_output, file=old_stderr)


def browser_check(
    url: str = "http://localhost:5173",
    mode: str = "inspect",
    viewport: dict | None = None,
    fresh: bool = False,
    wait: int = 2,
    actions: list | None = None,
    script: str | None = None,
    screenshot: bool = False,
    format: str = "json",
) -> str:
    """同步包装：browser_check 的同步入口。"""
    # FIX: LLM may pass wait as a string (e.g., "3" instead of 3).
    # Convert to int to prevent TypeError in max(wait, 3).
    try:
        wait = int(wait)
    except (ValueError, TypeError):
        wait = 2

    # FIX: LLM may pass actions as a JSON string instead of a list.
    # Parse it to prevent "'str' object has no attribute 'get'" error.
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except json.JSONDecodeError:
            actions = None

    async def _run() -> dict | str:
        return await _browser_check_async(
            url=url, mode=mode, viewport=viewport, fresh=fresh, wait=wait,
            actions=actions, script=script, screenshot=screenshot, format=format,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        result = _run_async_in_thread(_run())
    else:
        # 没有运行中的事件循环，直接 asyncio.run
        import io
        import sys
        old_stderr = sys.stderr
        captured = io.StringIO()
        sys.stderr = captured
        try:
            result = asyncio.run(_run())
        except _CLEANUP_EXC_TYPES as e:
            log.debug(f"[browser_check] suppressed cleanup race: {e}")
            result = {"error": "MCP connection cleanup race"}
        finally:
            sys.stderr = old_stderr
            stderr_output = captured.getvalue()
            if stderr_output and "stdio_client" not in stderr_output:
                print(stderr_output, file=old_stderr)

    # 统一返回字符串
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


# ─────────────────────────────────────────────────────────────────────────────
# 6. 兼容包装（deprecated，保留旧接口）
# ─────────────────────────────────────────────────────────────────────────────

async def _browser_test_compat_async(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    viewport: dict | None = None,
    start_command: str | None = None,
    port: int = 5173,
    startup_wait: int = 8,
) -> str:
    """兼容旧接口：browser_test → browser_check(mode="interact")"""
    from tools_impl import start_dev_server

    if start_command:
        server_result = start_dev_server(start_command, port, startup_wait)
        if server_result.startswith("[error]"):
            return server_result

    # 转换 actions 格式
    converted_actions = []
    for action in (actions or []):
        converted_actions.append({
            "type": action.get("type", ""),
            "selector": action.get("selector", ""),
            "value": action.get("value", action.get("script", "")),
            "delay": action.get("delay", 1000),
        })

    result = await _browser_check_async(
        url=url,
        mode="interact",
        viewport=viewport,
        fresh=True,  # 旧 browser_test 总是刷新
        wait=3,
        actions=converted_actions,
        screenshot=screenshot,
        format="text",
    )
    return str(result)


def browser_test_mcp(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    viewport: dict | None = None,
    context_id: str = "reviewer",
) -> str:
    """兼容旧接口（deprecated）—— 直接调用 browser_check。"""
    log.warning("[DEPRECATED] browser_test_mcp is deprecated, use browser_check instead")

    # 转换 actions 格式
    converted_actions = []
    for action in (actions or []):
        converted_actions.append({
            "type": action.get("type", ""),
            "selector": action.get("selector", ""),
            "value": action.get("value", action.get("script", "")),
            "delay": action.get("delay", 1000),
        })

    return browser_check(
        url=url,
        mode="interact",
        viewport=viewport,
        fresh=True,
        wait=3,
        actions=converted_actions,
        screenshot=screenshot,
        format="text",
    )


def browser_evaluate_mcp(
    script: str,
    url: str | None = None,
    viewport: dict | None = None,
    context_id: str = "reviewer",
) -> str:
    """兼容旧接口（deprecated）—— 直接调用 browser_check。"""
    log.warning("[DEPRECATED] browser_evaluate_mcp is deprecated, use browser_check instead")

    return browser_check(
        url=url or "http://localhost:5173",
        mode="inspect",
        viewport=viewport,
        fresh=True,
        wait=3,
        script=script,
        format="json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _find_ref_by_selector(snapshot: str, selector: str) -> str | None:
    """在 accessibility snapshot 中通过 selector 查找 ref。"""
    import re

    if not selector or not snapshot:
        return None

    search_text = selector.strip()
    for prefix in ("#", ".", "[", "]"):
        search_text = search_text.replace(prefix, " ")
    search_text = search_text.strip().lower()

    if not search_text:
        return None

    ref_pattern = re.compile(r'\[ref=([^\]]+)\]')
    candidates = []

    for line in snapshot.splitlines():
        line_lower = line.lower()
        ref_match = ref_pattern.search(line)
        if not ref_match:
            continue

        ref = ref_match.group(1)
        text_without_ref = ref_pattern.sub('', line).lower()

        if search_text in text_without_ref:
            quality = len(text_without_ref) - len(search_text)
            candidates.append((quality, ref, line.strip()))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    return None


def _summarize_snapshot(snapshot: str) -> dict:
    """从 snapshot 中提取关键信息摘要。"""
    lines = snapshot.splitlines()
    elements = []
    for line in lines:
        line = line.strip()
        if line.startswith("-") and "[ref=" in line:
            # 提取元素类型和文本
            parts = line.split("[ref=")
            if len(parts) >= 2:
                element_desc = parts[0].strip("- ").strip()
                elements.append(element_desc)

    return {
        "element_count": len(elements),
        "visible_elements": elements[:20],  # 前 20 个
    }


def _format_result_as_text(result: dict) -> str:
    """将结构化结果格式化为文本报告（兼容旧格式）。"""
    lines = []
    lines.append(f"Viewport: {result['viewport']['width']}x{result['viewport']['height']}")
    lines.append(f"URL: {result.get('url', 'N/A')}")
    lines.append(f"Title: {result.get('title', 'N/A')}")

    if "script_result" in result:
        lines.append(f"Script result: {json.dumps(result['script_result'], ensure_ascii=False)[:500]}")

    if "actions" in result:
        for action in result["actions"]:
            status = "✓" if action["status"] == "ok" else "✗"
            lines.append(f"  {status} {action['type']}")
            if "error" in action:
                lines.append(f"    Error: {action['error']}")
            if "result" in action:
                lines.append(f"    Result: {json.dumps(action['result'], ensure_ascii=False)[:200]}")

    if result.get("console_errors"):
        lines.append(f"Console errors ({len(result['console_errors'])}):")
        for err in result["console_errors"][:5]:
            lines.append(f"  - {err[:200]}")

    if "screenshot" in result:
        lines.append(f"Screenshot: {result['screenshot']}")

    if "error" in result:
        lines.append(f"[error] {result['error']}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 8. 生命周期管理（Round 开始/结束）
# ─────────────────────────────────────────────────────────────────────────────

async def close_all_sessions() -> None:
    """关闭所有浏览器会话（在 Round 结束时调用）。"""
    pool = BrowserSessionPool()
    await pool.close_all()


def close_all_sessions_sync() -> None:
    """同步包装：关闭所有浏览器会话。"""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(close_all_sessions()))
            future.result(timeout=30)
    except RuntimeError:
        asyncio.run(close_all_sessions())
