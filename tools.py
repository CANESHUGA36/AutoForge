"""
工具实现（含超时保护 + PIPE 修复）
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

import requests

import config
from skills import get_skill_path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# 全局线程池，用于工具级超时
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool_")

# 构建验证相关配置
_BUILDABLE_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js", ".css", ".scss"}
_server_pids: dict[int, int] = {}  # port -> pid

# list_files 缓存
_FILE_LIST_CACHE: dict[str, tuple[float, str]] = {}  # directory -> (mtime, result)
_DEFAULT_EXCLUDES = {
    ".git", "node_modules", ".next", "out", "dist",
    "__pycache__", ".venv", "venv", ".events",
    "*.pyc", "*.pyo", ".pytest_cache", ".coverage",
    "*.log", "*.tmp", ".DS_Store", "Thumbs.db",
}


def read_skill_file(name: str) -> str:
    """读取 skill 文件"""
    p = get_skill_path(name)
    if not p:
        return f"[error] Skill not found: {name}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"[error] Failed to read skill file: {e}"


def _resolve(path: str) -> Path:
    """解析路径并确保在工作目录内"""
    p = Path(config.WORKSPACE, path).resolve()
    ws = Path(config.WORKSPACE).resolve()
    if not str(p).startswith(str(ws)):
        raise ValueError(f"Path escapes workspace: {path}")
    return p


def read_file(path: str) -> str:
    """读取文件"""
    try:
        p = _resolve(path)
        if not p.exists():
            return f"[error] File not found: {path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        limit = 30_000
        if len(content) > limit:
            total = len(content)
            content = content[:limit] + (
                f"\n\n[TRUNCATED] You are seeing {limit} of {total} total characters."
            )
        return content
    except Exception as e:
        return f"[error] {e}"


def _auto_validate_build(path: str) -> str:
    """文件修改后自动验证构建（仅对有构建系统的项目）。"""
    # 只检查代码文件
    if not any(path.endswith(ext) for ext in _BUILDABLE_EXTENSIONS):
        return ""
    
    ws = Path(config.WORKSPACE)
    
    # 检测项目类型并执行相应验证
    if (ws / "package.json").exists():
        # Node.js 项目：先检查是否有构建错误
        # 使用快速检查（tsc --noEmit 或 next build）
        quick_check = "npx tsc --noEmit 2>&1 | head -20"
        result = run_bash(quick_check, timeout=60)
        
        if "error" in result.lower() and "0 errors" not in result.lower():
            # 有类型错误，但可能是配置问题，尝试构建
            pass
        
        # 关键：尝试生产构建
        build_result = run_bash("npm run build 2>&1 | tail -30", timeout=180)
        
        # 判断构建结果
        if "error" in build_result.lower() or "failed" in build_result.lower():
            if "0 errors" not in build_result.lower():
                return f"\n[BUILD WARNING] Production build has errors:\n{build_result[:800]}\n[NOTE] Please fix build errors before proceeding."
        
        if "compiled successfully" in build_result.lower() or "build succeeded" in build_result.lower():
            return "\n[BUILD OK] Production build succeeded."
    
    elif (ws / "requirements.txt").exists() or (ws / "pyproject.toml").exists():
        # Python 项目
        pass
    
    return ""


def write_file(path: str, content: str) -> str:
    """写入文件（含自动构建验证）。"""
    try:
        if not path or not path.strip():
            return "[error] Empty file path"
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        
        # 自动构建验证
        build_status = _auto_validate_build(path)
        
        return f"Wrote {len(content)} chars to {path}{build_status}"
    except Exception as e:
        return f"[error] {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """编辑文件"""
    try:
        p = _resolve(path)
        if not p.exists():
            if old_string == "":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(new_string, encoding="utf-8")
                return f"Created new file {path}"
            return f"[error] File not found: {path}"

        content = p.read_text(encoding="utf-8", errors="replace")

        if old_string not in content:
            lines_with_match = []
            for i, line in enumerate(content.splitlines(), 1):
                if old_string[:40] in line or (len(old_string) > 10 and old_string[:20] in line):
                    lines_with_match.append(f"  line {i}: {line.strip()[:100]}")
            hint = ""
            if lines_with_match:
                hint = "\nPartial matches found:\n" + "\n".join(lines_with_match[:3])
            return f"[error] old_string not found. Must match EXACTLY.{hint}"

        count = content.count(old_string)
        if count > 1:
            return f"[error] old_string appears {count} times. Add more context."

        new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content, encoding="utf-8")
        
        # 自动构建验证
        build_status = _auto_validate_build(path)
        
        return f"Edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars{build_status}"
    except Exception as e:
        return f"[error] {e}"


def _should_exclude(path: Path, workspace: Path, excludes: set[str]) -> bool:
    """判断路径是否应被排除。"""
    rel_str = str(path.relative_to(workspace))
    parts = rel_str.split(os.sep)
    
    for part in parts:
        # 检查目录名是否匹配排除列表
        if part in excludes:
            return True
        # 检查文件扩展名
        if any(part.endswith(ext.lstrip("*")) for ext in excludes if ext.startswith("*")):
            return True
    
    return False


def list_files(directory: str = ".", use_cache: bool = True) -> str:
    """列出文件（自动排除大目录 + 增量缓存）。
    
    自动排除: .git, node_modules, .next, out, dist, __pycache__,
    .venv, venv, .events, *.pyc, *.log 等。
    
    Args:
        directory: 要列出的目录，默认当前目录
        use_cache: 是否使用缓存，默认 True
    """
    try:
        p = _resolve(directory)
        if not p.is_dir():
            return f"[error] Not a directory: {directory}"
        
        ws = Path(config.WORKSPACE).resolve()
        cache_key = str(p.resolve())
        
        # 检查缓存是否有效
        if use_cache and cache_key in _FILE_LIST_CACHE:
            cached_mtime, cached_result = _FILE_LIST_CACHE[cache_key]
            # 检查目录修改时间
            try:
                current_mtime = p.stat().st_mtime
                if current_mtime <= cached_mtime:
                    return cached_result
            except Exception:
                pass  # 缓存检查失败，继续扫描
        
        entries = []
        scanned = 0
        max_scan = 5000  # 防止超大目录扫描
        
        for item in sorted(p.rglob("*")):
            scanned += 1
            if scanned > max_scan:
                entries.append(f"...[truncated: scanned {max_scan} items, more exist]")
                break
            
            if not item.is_file():
                continue
            
            # 排除检查
            if _should_exclude(item, ws, _DEFAULT_EXCLUDES):
                continue
            
            rel = item.relative_to(ws)
            entries.append(str(rel))
        
        if not entries:
            result = "(empty)"
        else:
            # 限制返回数量
            if len(entries) > 200:
                entries = entries[:200]
                entries.append(f"...[truncated: {len(entries)} total files]")
            result = "\n".join(entries)
        
        # 更新缓存
        try:
            _FILE_LIST_CACHE[cache_key] = (p.stat().st_mtime, result)
        except Exception:
            pass
        
        return result
    except Exception as e:
        return f"[error] {e}"


def run_bash(command: str, timeout: int = 900) -> str:
    """执行 bash 命令（修复 PIPE 死锁 + 后台命令支持 + Windows 兼容）。

    关键修复：
    1. 使用 Popen + 线程读取 stdout/stderr，避免 capture_output 导致的 PIPE 死锁
    2. Windows 兼容：不使用 start_new_session（Unix-only），改用 creationflags
    3. 对 npx/npm 初始化命令自动增加 timeout 预算
    4. 输出实时流式收集，防止缓冲区满阻塞子进程
    """
    command = command.strip()
    is_background = command.endswith("&") or " & " in command

    # 自动为项目初始化命令增加 timeout
    cmd_lower = command.lower()
    if any(kw in cmd_lower for kw in ["create-next-app", "create vite", "npx create"]):
        timeout = max(timeout, 600)

    try:
        if is_background:
            # 后台命令：彻底 detached，不捕获任何输出
            kwargs = {
                "shell": True,
                "cwd": config.WORKSPACE,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            # Windows 兼容：使用 CREATE_NEW_PROCESS_GROUP 替代 start_new_session
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(command, **kwargs)
            time.sleep(2)
            return f"[background] pid {proc.pid}: {command[:80]}"

        # 普通命令：使用 Popen + 线程读取，避免 PIPE 死锁
        kwargs = {
            "shell": True,
            "cwd": config.WORKSPACE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        # Windows 兼容
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(command, **kwargs)

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        def _read_stream(stream, chunks):
            try:
                for chunk in iter(lambda: stream.read(8192), b""):
                    chunks.append(chunk)
            except Exception:
                pass

        # 启动读取线程
        import threading
        t_out = threading.Thread(target=_read_stream, args=(proc.stdout, stdout_chunks))
        t_err = threading.Thread(target=_read_stream, args=(proc.stderr, stderr_chunks))
        t_out.start()
        t_err.start()

        # 等待进程完成（带超时）
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 超时：强制终止
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait()
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            return f"[error] Command timed out after {timeout}s"

        # 等待读取线程完成
        t_out.join(timeout=5)
        t_err.join(timeout=5)

        # 合并输出
        stdout_bytes = b"".join(stdout_chunks)
        stderr_bytes = b"".join(stderr_chunks)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        output = _smart_truncate(stdout, stderr)
        if proc.returncode != 0:
            output = f"[exit code: {proc.returncode}]\n{output}"
        return output or "(no output)"

    except Exception as e:
        return f"[error] {e}"


def _smart_truncate(stdout: str, stderr: str, limit: int = 10_000) -> str:
    """智能截断输出"""
    stderr = (stderr or "").strip()
    stdout = (stdout or "").strip()
    combined = (stdout + "\n" + stderr).strip() if stderr else stdout

    if len(combined) <= limit:
        return combined

    stderr_budget = min(len(stderr), int(limit * 0.4))
    stdout_budget = limit - stderr_budget

    if len(stderr) > stderr_budget:
        stderr = "...[stderr truncated]\n" + stderr[-(stderr_budget - 30):]

    if len(stdout) <= stdout_budget:
        truncated_stdout = stdout
    else:
        head_size = int(stdout_budget * 0.40)
        tail_size = int(stdout_budget * 0.40)
        middle_budget = stdout_budget - head_size - tail_size - 200

        head = stdout[:head_size]
        tail = stdout[-tail_size:]

        middle = stdout[head_size:-tail_size] if tail_size else stdout[head_size:]
        important_lines = []
        _error_pattern = re.compile(
            r'(?i)(error|fail|assert|exception|traceback|warning|not found|denied|refused|fatal)',
        )
        if middle and middle_budget > 0:
            for line in middle.splitlines():
                if _error_pattern.search(line):
                    important_lines.append(line)

        important_section = "\n".join(important_lines)
        if len(important_section) > middle_budget:
            important_section = important_section[:middle_budget]

        middle_part = ""
        if important_section:
            middle_part = (
                f"\n\n[...{len(middle)} chars omitted — key lines extracted:]\n"
                + important_section + "\n[...end extracted lines]\n\n"
            )
        else:
            middle_part = f"\n\n[TRUNCATED — {len(middle)} chars omitted]\n\n"

        truncated_stdout = head + middle_part + tail

    if stderr:
        return truncated_stdout + "\n\n--- STDERR ---\n" + stderr
    return truncated_stdout


def generate_image(
    prompt: str,
    path: str,
    aspect_ratio: str = "16:9",
) -> str:
    """Call MiniMax image-01 API and save JPEG bytes to the workspace."""
    try:
        api_key = (config.MINIMAX_API_KEY or "").strip()
        if not api_key:
            return "[error] MINIMAX_API_KEY not set (or OPENAI_API_KEY empty); add a key in .env"

        if not prompt or not prompt.strip():
            return "[error] prompt is required"
        if not path or not path.strip():
            return "[error] path is required"

        url = "https://api.minimaxi.com/v1/image_generation"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "image-01",
            "prompt": prompt.strip(),
            "aspect_ratio": aspect_ratio,
            "n": 1,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        try:
            data = response.json()
        except Exception:
            return f"[error] Non-JSON response (HTTP {response.status_code}): {response.text[:800]}"

        if response.status_code >= 400:
            err = data.get("message") or data.get("error") or data
            return f"[error] HTTP {response.status_code}: {err}"

        # Check base_resp status
        base_resp = data.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            status_msg = base_resp.get("status_msg", "unknown error")
            return f"[error] API error {status_code}: {status_msg}"

        inner = data.get("data") or {}

        # Try base64 format first
        b64_list = inner.get("image_base64")
        if b64_list and isinstance(b64_list, list):
            raw = base64.b64decode(b64_list[0])
        else:
            # Fallback to URL format - download the image
            url_list = inner.get("image_urls")
            if url_list and isinstance(url_list, list) and len(url_list) > 0:
                image_url = url_list[0]
                img_response = requests.get(image_url, timeout=60)
                if img_response.status_code != 200:
                    return f"[error] Failed to download image from URL (HTTP {img_response.status_code})"
                raw = img_response.content
            else:
                return f"[error] Unexpected API response: {str(data)[:1200]}"

        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)

        hint = ""
        low = path.lower()
        if low.endswith(".png"):
            hint = " Note: output is JPEG; use .jpg/.jpeg in path for correct MIME type."

        return f"Generated image saved to {path} ({len(raw)} bytes).{hint}"
    except Exception as e:
        return f"[error] {e}"


# ---------------------------------------------------------------------------
# MiniMax Coding Plan — Search & VLM
# ---------------------------------------------------------------------------

def _get_minimax_base_url() -> str:
    """根据环境返回正确的 MiniMax API 域名。"""
    # 复用 OPENAI_BASE_URL 的域名来判断 region
    base = getattr(config, "BASE_URL", "")
    if "minimaxi.com" in base:
        return "https://api.minimaxi.com"
    return "https://api.minimax.io"


def search_web(query: str, limit: int = 5) -> str:
    """Search the web using MiniMax Coding Plan search API.

    Args:
        query: Search query string.
        limit: Max number of results to return (default 5).

    Returns:
        Formatted search results or error message.
    """
    try:
        api_key = (config.MINIMAX_API_KEY or "").strip()
        if not api_key:
            return "[error] MINIMAX_API_KEY not set; cannot perform web search"

        if not query or not query.strip():
            return "[error] query is required"

        base_url = _get_minimax_base_url()
        url = f"{base_url}/v1/coding_plan/search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"q": query.strip()}

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        try:
            data = response.json()
        except Exception:
            return f"[error] Non-JSON response (HTTP {response.status_code}): {response.text[:800]}"

        # Check base_resp status (some endpoints use this)
        base_resp = data.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            status_msg = base_resp.get("status_msg", "unknown error")
            return f"[error] API error {status_code}: {status_msg}"

        # The search API returns results in "organic" array
        results = data.get("organic", [])
        if not results:
            return "No results found."

        # Format results
        lines = [f"Search results for: '{query}'", ""]
        for i, r in enumerate(results[:limit], 1):
            title = r.get("title", "No title")
            snippet = r.get("snippet", "")
            link = r.get("link", r.get("url", ""))
            lines.append(f"{i}. {title}")
            if snippet:
                lines.append(f"   {snippet[:300]}")
            if link:
                lines.append(f"   URL: {link}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"[error] {e}"


def analyze_image(image_path: str, prompt: str = "Describe this image in detail") -> str:
    """Analyze an image using MiniMax Coding Plan VLM API.

    Supports local file paths (relative to workspace) and URLs.

    Args:
        image_path: Path to image file (relative to workspace) or URL.
        prompt: Specific analysis instruction.

    Returns:
        VLM analysis result or error message.
    """
    try:
        api_key = (config.MINIMAX_API_KEY or "").strip()
        if not api_key:
            return "[error] MINIMAX_API_KEY not set; cannot analyze image"

        if not image_path or not image_path.strip():
            return "[error] image_path is required"

        base_url = _get_minimax_base_url()
        url = f"{base_url}/v1/coding_plan/vlm"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        image_path = image_path.strip()

        # Determine if it's a URL or local file
        if image_path.startswith("http://") or image_path.startswith("https://"):
            payload = {
                "prompt": prompt.strip(),
                "image_url": image_path,
            }
        else:
            # Local file - resolve and base64 encode
            try:
                p = _resolve(image_path)
            except ValueError:
                # Try as absolute path
                p = Path(image_path)
            if not p.exists():
                return f"[error] Image file not found: {image_path}"

            img_bytes = p.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(p))
            if not mime_type:
                mime_type = "image/jpeg"
            b64_data = base64.b64encode(img_bytes).decode("utf-8")
            payload = {
                "prompt": prompt.strip(),
                "image_url": f"data:{mime_type};base64,{b64_data}",
            }

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        try:
            data = response.json()
        except Exception:
            return f"[error] Non-JSON response (HTTP {response.status_code}): {response.text[:800]}"

        # Check base_resp status
        base_resp = data.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            status_msg = base_resp.get("status_msg", "unknown error")
            return f"[error] API error {status_code}: {status_msg}"

        # Extract result - VLM API returns content at top level
        result = data.get("content", "")
        if not result:
            result_data = data.get("data", {})
            if isinstance(result_data, dict):
                result = result_data.get("content", "")
                if not result:
                    result = result_data.get("result", "")
            else:
                result = str(result_data)
        if not result:
            result = data.get("result", "")
        if not result:
            result = str(data)

        return result
    except Exception as e:
        return f"[error] {e}"


def delegate_task(task: str, role: str = "assistant") -> str:
    """委派任务给子 Agent —— 核心上下文隔离机制。

    子 Agent 拥有独立的 messages 列表，其工具调用历史不会污染父 Agent 的上下文。
    这是防止 Builder 上下文爆炸的关键机制。
    """
    from agents import Agent
    from prompts import COMPONENT_BUILDER_SYSTEM

    # 根据 role 选择专业化 system prompt
    _role_prompts = {
        "component_builder": COMPONENT_BUILDER_SYSTEM,
    }

    if role in _role_prompts:
        system_prompt = _role_prompts[role]
    else:
        system_prompt = (
            f"You are a sub-agent with role: {role}. "
            f"Complete the task and provide a concise summary."
        )

    sub = Agent(
        name=f"sub_{role}",
        system_prompt=system_prompt,
        tools=TOOL_SCHEMAS
    )

    result = sub.run(task)
    if len(result) > 8000:
        result = result[:8000] + "\n...(truncated)"
    return result


# Playwright 浏览器测试 + Dev Server 管理
_dev_server_proc = None


def _kill_port(port: int) -> None:
    """强制释放指定端口（Windows + Linux/Mac 兼容）。"""
    # 1. 使用系统命令查找并终止占用端口的进程
    if os.name == "nt":
        # Windows: 使用 netstat + taskkill
        run_bash(
            f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{port}\') do taskkill /F /PID %a 2>nul',
            timeout=10,
        )
    else:
        # Linux/Mac: 使用 fuser 或 lsof
        run_bash(f"fuser -k {port}/tcp 2>/dev/null || lsof -ti:{port} | xargs kill -9 2>/dev/null; echo done", timeout=10)
    
    time.sleep(1)
    
    # 2. 清理全局记录的 dev server 进程
    global _dev_server_proc
    if _dev_server_proc is not None:
        try:
            if _dev_server_proc.poll() is None:
                if os.name == "nt":
                    _dev_server_proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    _dev_server_proc.terminate()
                _dev_server_proc.wait(timeout=3)
        except Exception:
            try:
                _dev_server_proc.kill()
                _dev_server_proc.wait(timeout=2)
            except Exception:
                pass
        _dev_server_proc = None
    
    # 3. 从 _server_pids 清理
    if port in _server_pids:
        try:
            if os.name == "nt":
                os.kill(_server_pids[port], signal.SIGTERM)
            else:
                os.kill(_server_pids[port], signal.SIGKILL)
        except Exception:
            pass
        del _server_pids[port]


def _kill_dev_server() -> None:
    """强制终止 dev server 进程（兼容旧代码）。"""
    _kill_port(3000)
    _kill_port(5173)


def start_dev_server(command: str = "npm run dev", port: int = 3000, wait: int = 10) -> str:
    """可靠启动 dev server，自动处理端口占用和缓存清理。
    
    这是 BrowserTester 启动服务器的首选方式，替代直接运行 `npm run dev &`。
    
    Args:
        command: 启动命令，默认 "npm run dev"
        port: 服务端口，默认 3000 (Next.js)
        wait: 启动后等待秒数，默认 10
    
    Returns:
        成功: "Server running on port X (pid Y)"
        失败: "[error] ..."
    """
    ws = Path(config.WORKSPACE)
    
    # 1. 释放端口
    _kill_port(port)
    time.sleep(1)
    
    # 2. 清理 Next.js 缓存（常见修复）
    next_cache = ws / ".next"
    if next_cache.exists():
        run_bash("rm -rf .next", timeout=30)
    
    # 3. 先验证构建（关键！）
    if (ws / "package.json").exists():
        build_result = run_bash("npm run build 2>&1 | tail -20", timeout=180)
        
        # 判断构建是否成功
        has_error = (
            "error" in build_result.lower() 
            and "0 errors" not in build_result.lower()
            and "compiled successfully" not in build_result.lower()
        )
        
        if has_error:
            return (
                f"[error] Build failed before starting server. "
                f"Fix build errors first:\n{build_result[:600]}"
            )
    
    # 4. 启动 server
    global _dev_server_proc
    kwargs = {
        "shell": True,
        "cwd": config.WORKSPACE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    _dev_server_proc = subprocess.Popen(command, **kwargs)
    _server_pids[port] = _dev_server_proc.pid
    
    # 5. 等待并健康检查
    time.sleep(wait)
    
    # 多次重试健康检查
    for attempt in range(3):
        health = run_bash(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}",
            timeout=10,
        )
        if health.strip() == "200":
            return f"Server running on port {port} (pid {_dev_server_proc.pid})"
        time.sleep(3)
    
    # 健康检查失败，返回错误但保留进程
    return (
        f"[error] Server started (pid {_dev_server_proc.pid}) but health check failed. "
        f"HTTP status: {health.strip()}. The build may have succeeded but the dev server has issues."
    )


def _browser_test_impl(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    start_command: str | None = None,
    port: int = 5173,
    startup_wait: int = 8,
    viewport: dict | None = None,
) -> str:
    """browser_test 的实际实现（在线程池中运行）。"""
    global _dev_server_proc

    if not HAS_PLAYWRIGHT:
        return "[error] Playwright not installed"

    vp = viewport if isinstance(viewport, dict) else {"width": 1280, "height": 720}
    report_lines = [f"Viewport: {vp['width']}x{vp['height']}"]

    if start_command:
        if _dev_server_proc is None or _dev_server_proc.poll() is not None:
            _kill_dev_server()
            kwargs = {
                "shell": True,
                "cwd": config.WORKSPACE,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            _dev_server_proc = subprocess.Popen(start_command, **kwargs)
            time.sleep(startup_wait)
        report_lines.append("Server started")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            page = browser.new_page(viewport=vp)

            try:
                page.goto(url, timeout=15000)
                report_lines.append(f"Navigated to {url} — title: {page.title()}")
            except Exception as e:
                report_lines.append(f"[error] Navigation failed: {e}")
                browser.close()
                return "\n".join(report_lines)

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            for action in (actions or []):
                action_type = action.get("type", "")
                selector = action.get("selector", "")
                value = action.get("value", "")
                delay = action.get("delay", 1000)

                try:
                    if action_type == "click":
                        page.click(selector, timeout=5000)
                        report_lines.append(f"Clicked: {selector}")
                    elif action_type == "fill":
                        page.fill(selector, value, timeout=5000)
                        report_lines.append(f"Filled '{selector}'")
                    elif action_type == "wait":
                        page.wait_for_timeout(delay)
                    elif action_type == "evaluate":
                        result = page.evaluate(value)
                        report_lines.append(f"JS eval: {str(result)[:500]}")
                    elif action_type == "scroll":
                        page.evaluate(f"window.scrollBy(0, {value or 500})")
                except Exception as e:
                    report_lines.append(f"[error] {action_type}: {e}")

                page.wait_for_timeout(300)

            report_lines.append(f"Final URL: {page.url}")
            report_lines.append(f"Visible text: {page.inner_text('body')[:2000]}")

            if console_errors:
                report_lines.append(f"Console errors ({len(console_errors)}):")
                for err in console_errors[:10]:
                    report_lines.append(f"  - {err[:200]}")

            if screenshot:
                ss_name = f"_screenshot_{vp['width']}x{vp['height']}.png"
                ss_path = Path(config.WORKSPACE) / ss_name
                page.screenshot(path=str(ss_path), full_page=False)
                report_lines.append(f"Screenshot saved to {ss_name}")

            browser.close()
    except Exception as e:
        report_lines.append(f"[error] Browser test failed: {e}")
    
    result = "\n".join(report_lines)
    result = _smart_truncate_browser_result(result)
    return result


def _smart_truncate_browser_result(result: str, max_chars: int = 4000) -> str:
    """浏览器测试结果智能截断——保留关键信息，丢弃冗余 HTML。
    
    策略：
    1. 保留所有元数据行（Viewport, Navigated, title, Screenshot）
    2. 保留错误行（[error], Console errors）
    3. 截断过长的 JS eval 结果（保留前 200 字）
    4. 截断 Visible text（保留前 300 字）
    """
    if len(result) <= max_chars:
        return result
    
    lines = result.splitlines()
    important_lines = []
    js_eval_budget = 800  # JS eval 总预算
    js_eval_used = 0
    visible_text_kept = False
    
    for line in lines:
        # 始终保留的关键行
        if any(line.startswith(prefix) for prefix in [
            "Viewport:", "Navigated to", "Final URL:", "Screenshot saved",
            "[error]", "Console errors", "Server started",
        ]):
            important_lines.append(line)
        # 截断 JS eval
        elif line.startswith("JS eval:"):
            if js_eval_used < js_eval_budget:
                content = line[8:].strip()  # 去掉 "JS eval: " 前缀
                if len(content) > 200:
                    line = f"JS eval: {content[:200]}..."
                important_lines.append(line)
                js_eval_used += len(line)
        # 只保留一次 Visible text
        elif line.startswith("Visible text:"):
            if not visible_text_kept:
                content = line[13:].strip()
                if len(content) > 300:
                    line = f"Visible text: {content[:300]}..."
                important_lines.append(line)
                visible_text_kept = True
        # 保留其他短行（可能是自定义输出）
        elif len(line) < 200:
            important_lines.append(line)
    
    summary = "\n".join(important_lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n...[TRUNCATED: key info preserved]"
    
    return summary


def browser_test(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    start_command: str | None = None,
    port: int = 5173,
    startup_wait: int = 8,
    viewport: dict | None = None,
) -> str:
    """Browser test with configurable viewport + 独立线程超时保护。

    Default viewport is 1280x720 (desktop).
    Pass viewport={"width": 375, "height": 812} for mobile testing.
    """
    future = _TOOL_EXECUTOR.submit(
        _browser_test_impl,
        url, actions, screenshot, start_command, port, startup_wait, viewport,
    )
    try:
        return future.result(timeout=120)  # 2 分钟硬上限
    except FutureTimeoutError:
        _kill_dev_server()
        return "[error] Browser test timed out after 120s (killed dev server)"
    except Exception as e:
        _kill_dev_server()
        return f"[error] Browser test failed: {e}"


def browser_evaluate(
    script: str,
    url: str | None = None,
    viewport: dict | None = None,
) -> str:
    """Execute JavaScript in a headless browser and return the result.
    
    Use this for precise DOM inspection when browser_test's bundled evaluate
    is not sufficient. The browser instance is ephemeral (opens, evaluates, closes).
    
    Args:
        script: JavaScript code to execute. Use return statement for values.
                Example: "return document.querySelectorAll('.card').length"
        url: Optional URL to navigate to before evaluation. If omitted,
             assumes a dev server is already running on localhost.
        viewport: Optional viewport size. Default: {"width": 1280, "height": 720}
    """
    if not HAS_PLAYWRIGHT:
        return "[error] Playwright not installed"
    
    vp = viewport if isinstance(viewport, dict) else {"width": 1280, "height": 720}
    
    def _impl():
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
                )
                page = browser.new_page(viewport=vp)
                
                if url:
                    try:
                        page.goto(url, timeout=15000)
                    except Exception as e:
                        browser.close()
                        return f"[error] Navigation failed: {e}"
                
                try:
                    result = page.evaluate(script)
                    browser.close()
                    return f"Result: {json.dumps(result, ensure_ascii=False, default=str)}"
                except Exception as e:
                    browser.close()
                    return f"[error] Script execution failed: {e}"
        except Exception as e:
            return f"[error] Browser failed: {e}"
    
    future = _TOOL_EXECUTOR.submit(_impl)
    try:
        return future.result(timeout=30)
    except FutureTimeoutError:
        return "[error] browser_evaluate timed out after 30s"
    except Exception as e:
        return f"[error] browser_evaluate failed: {e}"


# 工具 Schemas
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file in the workspace",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing a string",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files in a directory",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "default": "."}}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Execute a bash command. For background servers (e.g. 'npm run dev &'), the command is detached automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 900}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_file",
            "description": "Read a SKILL.md file from the skills directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the skill directory"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image with MiniMax image-01 and save it under the workspace. "
                "Use for hero banners, icons, backgrounds, sprites, avatars, etc. "
                "Write detailed prompts (subject, art style, lighting, color palette, mood). "
                "API returns JPEG bytes; prefer path ending in .jpg or .jpeg."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Image prompt in English or Chinese (subject, style, lighting, colors).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path, e.g. assets/hero.jpg or public/logo.jpeg",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Aspect ratio such as 1:1, 16:9, 9:16, 4:3, 3:2, 2:3",
                        "default": "16:9",
                    },
                },
                "required": ["prompt", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for information, design references, documentation, or examples. "
                "Use when you need to research a topic, find official websites, gather design inspiration, "
                "or look up API documentation. Returns structured search results with titles, snippets, and URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be specific for better results.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
]

BROWSER_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browser_test",
            "description": (
                "Test the app in a headless Chromium browser. "
                "Call twice for web apps: once with default viewport (desktop 1280x720) "
                "and once with viewport={\"width\": 375, \"height\": 812} for mobile. "
                "For each call, provide one action per functional criterion to verify."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "description": (
                            "List of actions. Each action is an object with 'type' "
                            "(click|fill|wait|evaluate|scroll), optional 'selector', 'value', 'delay'. "
                            "Use evaluate to run JS and capture return values."
                        ),
                    },
                    "screenshot": {"type": "boolean", "default": True},
                    "start_command": {
                        "type": "string",
                        "description": "DEPRECATED: Use start_dev_server() instead. This field is kept for backward compatibility."
                    },
                    "viewport": {
                        "type": "object",
                        "description": "Browser viewport size. Default: {\"width\": 1280, \"height\": 720}. Use {\"width\": 375, \"height\": 812} for mobile.",
                        "properties": {
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": (
                "Execute JavaScript in a headless browser and return the result. "
                "Use for precise DOM inspection, element counting, style verification, "
                "or any check that needs exact JS return values. "
                "The browser opens, evaluates, and closes immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "JavaScript to execute. Use 'return' statement for values. Example: 'return document.querySelectorAll(\\'.card\\').length'",
                    },
                    "url": {
                        "type": "string",
                        "description": "Optional: URL to navigate to before evaluation. If omitted, assumes dev server is running.",
                    },
                    "viewport": {
                        "type": "object",
                        "description": "Browser viewport. Default: {\"width\": 1280, \"height\": 720}.",
                        "properties": {
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_dev_server",
            "description": (
                "Reliably start the dev server with build verification and cache cleanup. "
                "This is the PREFERRED way to start servers for browser testing. "
                "It handles: port conflict resolution, .next cache cleanup, build validation, "
                "and health checks. Do NOT use `npm run dev &` directly — use this tool instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Start command. Default: 'npm run dev' (Next.js). For Vite: 'npm run dev'. For static: 'npx serve -s . -l 3000'.",
                        "default": "npm run dev",
                    },
                    "port": {
                        "type": "integer",
                        "description": "Server port. Default: 3000 (Next.js). For Vite: 5173.",
                        "default": 3000,
                    },
                    "wait": {
                        "type": "integer",
                        "description": "Seconds to wait after starting. Default: 10.",
                        "default": 10,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": (
                "Analyze an image using a vision-language model (VLM). "
                "Use to verify visual design quality, check color accuracy, verify layout composition, "
                "or compare against design references. Supports local image files (relative to workspace) and URLs. "
                "Ideal for analyzing browser_test screenshots or generated images."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to image file (relative to workspace) or URL. For screenshots from browser_test, use the screenshot filename (e.g., '_screenshot_1280x720.png').",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Specific analysis instruction. Example: 'Evaluate the visual design quality, color palette, and layout against a dark fantasy RPG aesthetic.'",
                        "default": "Describe this image in detail",
                    },
                },
                "required": ["image_path"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "run_bash": run_bash,
    "browser_test": browser_test,
    "browser_evaluate": browser_evaluate,
    "read_skill_file": read_skill_file,
    "generate_image": generate_image,
    "start_dev_server": start_dev_server,
    "search_web": search_web,
    "analyze_image": analyze_image,
}


def execute_tool(name: str, arguments: dict) -> str:
    """执行工具"""
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return f"[error] Unknown tool: {name}"
    try:
        return fn(**arguments)
    except Exception as e:
        return f"[error] {type(e).__name__}: {e}"
