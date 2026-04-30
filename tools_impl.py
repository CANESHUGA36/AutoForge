"""Tools implementation (with timeout protection + PIPE fix)"""
from __future__ import annotations
import base64
import json
import logging
import mimetypes
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
import requests
import config
from skills import get_skill_path

log = logging.getLogger("harness")

_BUILDABLE_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js", ".css", ".scss"}
_dev_server_proc = None
_FILE_LIST_CACHE: dict[str, tuple[float, str]] = {}
_LAST_BUILD_CHECK: float = 0.0
_BUILD_CHECK_COOLDOWN: float = 30.0
# FIX: Cache build results to avoid redundant rebuilds
_BUILD_RESULT_CACHE: dict[str, tuple[float, str]] = {}
_BUILD_CACHE_TTL: float = 15.0  # Cache build results for 15 seconds
_DEFAULT_EXCLUDES = {
    ".git", "node_modules", ".next", "out", "dist",
    "__pycache__", ".venv", "venv", ".events",
    "*.pyc", "*.pyo", ".pytest_cache", ".coverage",
    "*.log", "*.tmp", ".DS_Store", "Thumbs.db",
}

def read_skill_file(name: str) -> str:

    """    skill    """

    p = get_skill_path(name)

    if not p:

        return f"[error] Skill not found: {name}"

    try:

        return p.read_text(encoding="utf-8")

    except Exception as e:

        return f"[error] Failed to read skill file: {e}"




def _resolve(path: str) -> Path:
    """"""
    p = Path(config.WORKSPACE, path).resolve()
    ws = Path(config.WORKSPACE).resolve()
    if not str(p).startswith(str(ws)):
        raise ValueError(f"Path escapes workspace: {path}")
    return p



def read_file(path: str) -> str:

    """"""
    try:

        p = _resolve(path)

        if not p.exists():

            return f"[error] File not found: {path}"

        content = p.read_text(encoding="utf-8", errors="replace")

        # FIX: Increase limit for single-file projects (HTML/JS/CSS merged into one file)
        # Large single files are common in pure-HTML projects and must be read fully
        # to avoid code duplication and corruption during iterative editing.
        limit = 80_000 if p.suffix in (".html", "htm") else 30_000

        if len(content) > limit:

            total = len(content)

            content = content[:limit] + (

                f"\n\n[TRUNCATED] You are seeing {limit} of {total} total characters."

            )

        return content

    except Exception as e:

        return f"[error] {e}"




def _update_workspace_build_status(status: str, error_msg: str = "") -> None:
    """Update WorkspaceState build status after explicit validation."""
    try:
        from workspace_state import WorkspaceState
        state = WorkspaceState.load(config.WORKSPACE)
        state.last_build_status = status
        if status == "ok":
            state.last_build_errors = []
        elif error_msg:
            state.last_build_errors.append(error_msg)
        state.save(config.WORKSPACE)
    except Exception:
        pass


def _validate_css_classes(expected_classes: list[str] | None = None) -> str:
    """验证生成的 CSS 是否包含预期的自定义类名。"""
    ws = Path(config.WORKSPACE)
    css_files = list(ws.glob("dist/assets/*.css"))
    if not css_files:
        return ""
    try:
        css_content = css_files[0].read_text(encoding="utf-8")
        expected = expected_classes or ["bg-background", "text-primary", "border-primary"]
        missing = [c for c in expected if f".{c}" not in css_content and f"{c}:" not in css_content]
        if missing:
            return f"\n[CSS WARNING] Missing classes: {', '.join(missing)}. Check Tailwind/CSS config. (Non-blocking)"
        return f"\n[CSS OK] All {len(expected)} expected classes found."
    except Exception:
        return ""


def validate_build() -> str:
    """Explicitly run build validation and return the result."""
    global _BUILD_RESULT_CACHE
    ws = Path(config.WORKSPACE)
    # Pure HTML projects don't need build validation
    if (ws / "index.html").exists() and not (ws / "package.json").exists():
        return "[BUILD OK] Pure HTML project — no build step required."
    if (ws / "package.json").exists():
        # FIX: Check build cache to avoid redundant rebuilds within TTL
        cache_key = str(ws.resolve())
        now = time.time()
        if cache_key in _BUILD_RESULT_CACHE:
            cached_time, cached_result = _BUILD_RESULT_CACHE[cache_key]
            if now - cached_time < _BUILD_CACHE_TTL:
                return cached_result
        # FIX BUG #4: Use exit code as primary signal, heuristic as fallback
        # Cross-platform: avoid Unix tail command, truncate in Python
        build_full = run_bash("npm run build 2>&1", timeout=180)
        build_lines = build_full.splitlines()
        build_result = "\n".join(build_lines[-40:]) if len(build_lines) > 40 else build_full
        # run_bash returns [exit code: N] on non-zero exit
        exit_code_match = __import__('re').search(r'\[exit code:\s*(\d+)\]', build_result)
        exit_code = int(exit_code_match.group(1)) if exit_code_match else None
        
        if exit_code is not None and exit_code != 0:
            _update_workspace_build_status("error", build_result[:300])
            return f"[BUILD WARNING] Production build failed (exit code {exit_code}):\n{build_result[:800]}\n[NOTE] Please fix build errors before proceeding."
        
        # Fallback heuristic when exit code not captured
        has_real_error = (
            exit_code is None
            and "error" in build_result.lower()
            and "0 errors" not in build_result.lower()
            and "compiled successfully" not in build_result.lower()
            and "build succeeded" not in build_result.lower()
        )
        if has_real_error:
            _update_workspace_build_status("error", build_result[:300])
            return f"[BUILD WARNING] Production build has errors:\n{build_result[:800]}\n[NOTE] Please fix build errors before proceeding."
        _update_workspace_build_status("ok")
        css_check = _validate_css_classes()
        # Build succeeded (exit code = 0 and no real errors detected)
        result = f"[BUILD OK] Production build succeeded.{css_check}"
        # FIX: Cache successful build result
        _BUILD_RESULT_CACHE[cache_key] = (time.time(), result)
        return result
    elif (ws / "requirements.txt").exists() or (ws / "pyproject.toml").exists():
        return "[BUILD INFO] Python project detected - no npm build available."
    return "[BUILD INFO] No package.json found - skipping build validation."


def _auto_validate_build(path: str) -> str:
    """Debounced auto-validation: only check if cooldown has passed."""
    global _LAST_BUILD_CHECK
    if not any(path.endswith(ext) for ext in _BUILDABLE_EXTENSIONS):
        return ""
    now = time.time()
    if now - _LAST_BUILD_CHECK < _BUILD_CHECK_COOLDOWN:
        return ""
    _LAST_BUILD_CHECK = now
    return validate_build()


def _trigger_vite_hmr() -> None:
    """Trigger Vite HMR recompile by touching entry files.

    Python's write_text() may not trigger chokidar file watchers reliably,
    especially for large files. Touching entry files forces Vite to
    invalidate its module graph and recompile.
    """
    ws = Path(config.WORKSPACE)
    for entry in (ws / "src" / "main.tsx", ws / "src" / "main.jsx",
                  ws / "src" / "index.tsx", ws / "src" / "App.tsx"):
        if entry.exists():
            try:
                entry.touch(exist_ok=True)
            except Exception:
                pass


def write_file(path: str, content: str) -> str:
    """"""
    try:
        if not path or not path.strip():
            return "[error] Empty file path"
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Check if content actually changed to avoid unnecessary rebuilds
        content_changed = True
        if p.exists():
            existing = p.read_text(encoding="utf-8", errors="replace")
            content_changed = existing != content
        p.write_text(content, encoding="utf-8")
        # FIX: Trigger Vite HMR so dev server picks up the change immediately
        _trigger_vite_hmr()
        # Only auto-validate if content actually changed
        build_status = ""
        if content_changed:
            build_status = _auto_validate_build(path)
        return f"Wrote {len(content)} chars to {path}{build_status}"
    except Exception as e:
        return f"[error] {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:

    """"""
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
            # Try fuzzy matching: normalize whitespace and try again
            import re
            normalized_content = re.sub(r'\s+', ' ', content).strip()
            normalized_old = re.sub(r'\s+', ' ', old_string).strip()
            if normalized_old in normalized_content:
                # Find the actual substring in original content that matches
                # Use a sliding window approach
                old_len = len(old_string)
                for i in range(len(content) - old_len + 1):
                    window = content[i:i + old_len]
                    if re.sub(r'\s+', ' ', window).strip() == normalized_old:
                        actual_old = window
                        new_content = content.replace(actual_old, new_string, 1)
                        p.write_text(new_content, encoding="utf-8")
                        _trigger_vite_hmr()
                        build_status = _auto_validate_build(path)
                        return f"Edited {path} (fuzzy match){build_status}"
            
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

        # FIX: Trigger Vite HMR so dev server picks up the change immediately
        _trigger_vite_hmr()

        build_status = _auto_validate_build(path)

        return f"Edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars{build_status}"

    except Exception as e:

        return f"[error] {e}"




def _should_exclude(path: Path, workspace: Path, excludes: set[str]) -> bool:
    """"""
    rel_str = str(path.relative_to(workspace))
    parts = rel_str.split(os.sep)
    for part in parts:
        if part in excludes:
            return True
        if any(part.endswith(ext.lstrip("*")) for ext in excludes if ext.startswith("*")):
            return True
    return False

def list_files(directory: str = ".", use_cache: bool = True) -> str:
    """"""
    try:
        p = _resolve(directory)
        if not p.is_dir():
            return f"[error] Not a directory: {directory}"
        ws = Path(config.WORKSPACE).resolve()
        cache_key = str(p.resolve())
        if use_cache and cache_key in _FILE_LIST_CACHE:
            cached_mtime, cached_result = _FILE_LIST_CACHE[cache_key]
            try:
                current_mtime = p.stat().st_mtime
                if current_mtime <= cached_mtime:
                    return cached_result
            except Exception:
                pass
        entries = []
        scanned = 0
        for entry in p.rglob("*"):
            scanned += 1
            if scanned > 10_000:
                entries.append("... (truncated after 10k entries)")
                break
            if _should_exclude(entry, ws, _DEFAULT_EXCLUDES):
                continue
            rel = entry.relative_to(ws)
            prefix = "D" if entry.is_dir() else "F"
            entries.append(f"{prefix}  {rel}")
        result = "\n".join(sorted(entries))
        try:
            _FILE_LIST_CACHE[cache_key] = (p.stat().st_mtime, result)
        except Exception:
            pass
        return result
    except Exception as e:
        return f"[error] {e}"



def _smart_truncate(stdout: str, stderr: str, limit: int = 10_000) -> str:

    """"""
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

                f"\n\n[...{len(middle)} chars omitted  ?key lines extracted:]\n"

                + important_section + "\n[...end extracted lines]\n\n"

            )

        else:

            middle_part = f"\n\n[TRUNCATED  ?{len(middle)} chars omitted]\n\n"

        truncated_stdout = head + middle_part + tail

    if stderr:

        return truncated_stdout + "\n\n--- STDERR ---\n" + stderr

    return truncated_stdout




_PROTECTED_PATHS = [
    ".eval_cache", ".events", "logs", ".git", ".workspace_state.json",
    "harness_state.json", "contract.md", "spec.md", "sprint.md", "feedback.md",
]

def _is_destructive_command(command: str) -> tuple[bool, str]:
    """Check if a command would delete protected system directories."""
    cmd_lower = command.lower()
    # Detect rm -rf, rm -r, del /s patterns
    is_removal = any(pattern in cmd_lower for pattern in [
        "rm -rf ", "rm -r ", "rmdir /s", "del /s", "remove-item -recurse",
    ])
    if not is_removal:
        return False, ""
    for protected in _PROTECTED_PATHS:
        if protected.lower() in cmd_lower:
            return True, protected
    return False, ""

def run_bash(command: str, timeout: int = 900) -> str:
    command = command.strip()
    
    # Block destructive commands against protected paths
    is_destructive, protected_path = _is_destructive_command(command)
    if is_destructive:
        return (
            f"[error] Command blocked: would delete protected path '{protected_path}'. "
            f"The following paths are protected and cannot be removed: "
            f"{', '.join(_PROTECTED_PATHS)}. "
            f"If you need to clean build artifacts, only remove: node_modules, dist, build, .next, *.log"
        )
    
    # Block git commands — Harness manages git automatically
    cmd_lower = command.lower()
    # Match git commands but allow "git log" for read-only inspection
    git_match = re.search(r'\bgit\s+([a-z]+)', cmd_lower)
    if git_match:
        git_subcmd = git_match.group(1)
        allowed_git_cmds = {"log", "status", "diff", "show", "blame"}
        if git_subcmd not in allowed_git_cmds:
            return (
                f"[error] Git command 'git {git_subcmd}' is blocked. "
                f"Harness automatically handles git commits and branch management. "
                f"You do not need to run git commands. "
                f"Allowed read-only commands: git log, git status, git diff"
            )
    
    is_background = command.endswith("&") or " & " in command
    cmd_lower = command.lower()
    if any(kw in cmd_lower for kw in ["create-next-app", "create vite", "npx create"]):
        timeout = max(timeout, 600)
    
    # FIX: Short timeout for simple version/check commands to prevent hanging
    simple_check_commands = ["tsc --version", "node --version", "npm --version", "vite --version"]
    if any(cmd in command.lower() for cmd in simple_check_commands) and timeout > 30:
        timeout = 30
    try:
        if is_background:
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
            proc = subprocess.Popen(command, **kwargs)
            time.sleep(2)
            return f"[background] pid {proc.pid}: {command[:80]}"
        kwargs = {
            "shell": True,
            "cwd": config.WORKSPACE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
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
        import threading
        t_out = threading.Thread(target=_read_stream, args=(proc.stdout, stdout_chunks))
        t_err = threading.Thread(target=_read_stream, args=(proc.stderr, stderr_chunks))
        t_out.start()
        t_err.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
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
        t_out.join(timeout=5)
        t_err.join(timeout=5)
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



def _get_minimax_base_url() -> str:
    """"""
    base = getattr(config, "BASE_URL", "")
    if "minimaxi.com" in base:
        return "https://api.minimaxi.com"
    return "https://api.minimax.io"

def generate_image(

    prompt: str,

    path: str,

    aspect_ratio: str = "16:9",

) -> str:

    """Call MiniMax image-01 API and save JPEG bytes to the workspace."""

    try:

        api_key = (config.GENERATE_IMAGE_API_KEY or "").strip()

        if not api_key:

            return "[error] GENERATE_IMAGE_API_KEY not set (or OPENAI_API_KEY empty); add a key in .env"

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

# MiniMax Coding Plan  ?Search & VLM

# ---------------------------------------------------------------------------




def search_web(query: str, limit: int = 5) -> str:

    """"""
    try:

        api_key = (config.API_KEY or "").strip()

        if not api_key:

            return "[error] API_KEY not set; cannot perform web search"

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

    """"""
    try:

        api_key = (config.API_KEY or "").strip()

        if not api_key:

            return "[error] API_KEY not set; cannot analyze image"

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




def _get_template_path(template: str) -> Path | None:
    """Resolve template path, supporting both Docker and Windows environments."""
    # Docker paths (Unix absolute)
    docker_paths = {
        "vite-react-ts": "/templates/template-vite-react-ts",
        "nextjs-app": "/templates/template-nextjs-app",
        "pure-html": "/templates/template-pure-html",
    }
    # Windows fallback: check relative to project root
    win_paths = {
        "vite-react-ts": Path("templates/template-vite-react-ts").resolve(),
        "nextjs-app": Path("templates/template-nextjs-app").resolve(),
        "pure-html": Path("templates/template-pure-html").resolve(),
    }
    # Check Docker path first
    docker_path_str = docker_paths.get(template)
    if docker_path_str:
        docker_path = Path(docker_path_str)
        if docker_path.exists():
            return docker_path
    # Fallback to Windows path
    win_path = win_paths.get(template)
    if win_path and win_path.exists():
        return win_path
    return None


def project_init(template: str) -> str:
    """Initialize project by copying a pre-cached template."""
    import shutil
    ws = Path(config.WORKSPACE)
    src_path = _get_template_path(template)
    if src_path is None:
        available = ["vite-react-ts", "nextjs-app", "pure-html"]
        return (
            f"[error] Template not found: {template}. "
            f"Available: {available}. "
            f"Templates are pre-cached in Docker. On Windows, run from Docker or create project manually."
        )
    try:
        # Copy template files to workspace
        for item in src_path.iterdir():
            dest = ws / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        # Clean up any pre-existing corrupted node_modules from template copy
        # to force a fresh install (fixes broken symlinks / partial installs)
        nm_path = ws / "node_modules"
        lock_path = ws / "package-lock.json"
        if nm_path.exists():
            shutil.rmtree(nm_path)
        if lock_path.exists():
            lock_path.unlink()
        # Pure HTML template needs no npm install
        if template == "pure-html":
            return (
                f"[BUILD OK] Project initialized from {template} template.\n"
                f"No build step required — write your code directly to index.html.\n\n"
                f"--- Environment Verification ---\n"
                f"✓ Pure HTML — no dependencies needed"
            )
        
        # Run npm install
        install_result = run_bash("npm install 2>&1", timeout=180)
        
        # FIX: Verify environment integrity after init
        verification = []
        
        # Check TypeScript
        tsc_check = run_bash("npx tsc --version 2>&1", timeout=30)
        if "Version" in tsc_check:
            verification.append(f"✓ TypeScript: {tsc_check.strip()}")
        else:
            verification.append(f"✗ TypeScript check failed: {tsc_check[:200]}")
            # Try to fix
            run_bash("npm install typescript --save-dev 2>&1", timeout=60)
        
        # Check key dependencies (cross-platform: avoid Unix head/tail)
        deps_check = run_bash("npm ls react vite 2>&1", timeout=30)
        deps_lines = deps_check.splitlines()[:5]
        deps_summary = "\n".join(deps_lines)
        if "empty" not in deps_summary.lower():
            verification.append(f"✓ Key dependencies installed")
        else:
            verification.append(f"✗ Dependencies missing")
        
        # Check build works — STRICT: fail fast if build doesn't pass
        # Cross-platform: capture full output and truncate in Python instead of using tail
        build_full = run_bash("npm run build 2>&1", timeout=180)
        build_lines = build_full.splitlines()
        build_check = "\n".join(build_lines[-20:]) if len(build_lines) > 20 else build_full
        exit_code_match = __import__('re').search(r'\[exit code:\s*(\d+)\]', build_check)
        exit_code = int(exit_code_match.group(1)) if exit_code_match else None
        build_has_error = (
            (exit_code is not None and exit_code != 0)
            or ("error" in build_check.lower()
                and "0 errors" not in build_check.lower()
                and "compiled successfully" not in build_check.lower()
                and "build succeeded" not in build_check.lower())
        )
        if build_has_error:
            env_verify = "\n".join(verification)
            return (
                f"[error] Project initialized from {template} template, "
                f"but build VERIFICATION FAILED.\n"
                f"Build output:\n{build_check[:800]}\n\n"
                f"--- Environment Verification ---\n"
                + env_verify +
                f"\n✗ Build failed — environment is unstable.\n"
                f"[ACTION REQUIRED] Do NOT write product code. "
                f"The environment must be fixed first (re-run project_init or change template)."
            )
        
        verification.append(f"✓ Build passes")
        env_verify = "\n".join(verification)
        return (
            f"[BUILD OK] Project initialized from {template} template.\n"
            f"{install_result}\n\n"
            f"--- Environment Verification ---\n"
            + env_verify
        )
    except Exception as e:
        return f"[error] Failed to initialize project: {e}"


# Playwright        ?+ Dev Server    

_dev_server_proc = None




def _kill_port(port: int) -> None:
    """"""
    if os.name == "nt":
        run_bash(
            f'for /f "tokens=5" %a in ("netstat -ano ^| findstr :{port}") do taskkill /F /PID %a 2>nul',
            timeout=10,
        )
    else:
        run_bash(f"fuser -k {port}/tcp 2>/dev/null || lsof -ti:{port} | xargs kill -9 2>/dev/null; echo done", timeout=10)

def _kill_dev_server() -> None:
    """Kill dev server and clear build caches to prevent stale content."""
    # FIX: Kill ALL common dev server ports to prevent multiple Vite processes
    # from competing for the same cache directory.
    for p in (3000, 5173, 5174, 5175, 5176, 5177, 5178, 5179, 5180,
              5181, 5182, 5183, 5184, 5185, 5186, 5187, 5188, 5189, 5190):
        _kill_port(p)
    # Also kill any process holding vite-related file locks
    if os.name != "nt":
        run_bash("pkill -f 'vite' 2>/dev/null || true", timeout=10)
    # Give processes time to release file locks before clearing cache
    time.sleep(2)
    # FIX: Clear Turbopack dev cache so the next dev server restart
    # picks up the latest file changes instead of serving stale compiled output.
    ws = Path(config.WORKSPACE)
    for cache_dir in (ws / ".next" / "cache", ws / ".next" / "turbopack"):
        if cache_dir.exists():
            try:
                import shutil
                shutil.rmtree(cache_dir, ignore_errors=True)
            except Exception:
                pass
    # FIX: Clear Vite cache to prevent stale pre-bundled deps / HMR state
    for vite_cache in (ws / "node_modules" / ".vite", ws / ".vite"):
        if vite_cache.exists():
            try:
                import shutil
                shutil.rmtree(vite_cache, ignore_errors=True)
                log.info(f"[dev_server] Cleared Vite cache ({vite_cache})")
            except Exception:
                pass
    # FIX: Clear Vite dist output to force full recompile on next dev start
    dist_dir = ws / "dist"
    if dist_dir.exists():
        try:
            import shutil
            shutil.rmtree(dist_dir, ignore_errors=True)
            log.info("[dev_server] Cleared dist/")
        except Exception:
            pass
    # FIX: Touch main entry file to force Vite to invalidate its module graph
    for entry in (ws / "src" / "main.tsx", ws / "src" / "main.jsx", ws / "src" / "index.tsx"):
        if entry.exists():
            try:
                entry.touch(exist_ok=True)
            except Exception:
                pass

def _restart_dev_server_if_running() -> None:
    """If a dev server is already running, kill and restart it to pick up latest file changes.
    
    This is called automatically after Builder writes/edits files to ensure Vite serves
    the latest code instead of stale cached modules.
    """
    global _dev_server_proc
    if _dev_server_proc is None:
        return
    try:
        # Check if process is still alive
        if _dev_server_proc.poll() is not None:
            _dev_server_proc = None
            return
    except Exception:
        _dev_server_proc = None
        return
    
    log.info("[dev_server] Auto-restarting after file change...")
    _kill_dev_server()
    time.sleep(2)
    
    # Restart with same parameters
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
    
    # Use --force for Vite to invalidate cache
    cmd = "npm run dev --force"
    _dev_server_proc = subprocess.Popen(cmd, **kwargs)
    time.sleep(8)  # Shorter wait for restart vs cold start
    log.info("[dev_server] Auto-restart complete")


def start_dev_server(command: str = "npm run dev", port: int = 3000, wait: int = 15) -> str:
    """启动 dev server。

    注意：BuildGateStage 已经验证过构建通过，此处不再重复运行 npm run build，
    也不再无条件删除 .next 缓存（这会强制 Next.js 重新编译，显著增加启动时间）。
    """
    global _dev_server_proc
    ws = Path(config.WORKSPACE)
    _kill_dev_server()
    time.sleep(1)
    
    # FIX: For Vite projects, add --force flag to invalidate dependency pre-bundling cache.
    # This ensures stale compiled modules are re-built from disk files.
    # Only add if command looks like a Vite dev command and doesn't already have --force.
    effective_command = command
    if "vite" in command.lower() or "npm run dev" in command.lower() or "npx vite" in command.lower():
        if "--force" not in command:
            effective_command = command + " --force"
            log.info(f"[dev_server] Added --force flag for Vite cache invalidation: {effective_command}")
    
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
    _dev_server_proc = subprocess.Popen(effective_command, **kwargs)
    time.sleep(wait)
    try:
        import urllib.request
        req = urllib.request.Request(f"http://localhost:{port}", method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return f"Server running on port {port}"
            return f"[error] Server started but health check failed. HTTP status: {resp.status}."
    except Exception as e:
        return f"[error] Health check failed: {e}"

def browser_check(
    url: str = "http://localhost:5173",
    mode: str = "inspect",
    viewport: dict | None = None,
    fresh: bool = False,
    wait: int = 2,
    actions: list | None = None,
    script: str | None = None,
    screenshot: bool = False,
) -> str:
    """Unified browser check using Playwright MCP.
    
    Modes:
      - inspect: Execute JS script and return result (replaces browser_evaluate)
      - interact: Execute action chain like clicks, fills, etc (replaces browser_test)
      - screenshot: Take screenshot for visual verification
    
    Args:
        url: Target URL
        mode: "inspect" | "interact" | "screenshot"
        viewport: {"width": int, "height": int}
        fresh: True = clear all caches and force refresh
        wait: Seconds to wait after navigation
        actions: List of action dicts for interact mode
        script: JS script for inspect mode
        screenshot: Whether to take screenshot
    """
    from tools.playwright_mcp import browser_check as _browser_check
    try:
        return _browser_check(
            url=url,
            mode=mode,
            viewport=viewport,
            fresh=fresh,
            wait=wait,
            actions=actions,
            script=script,
            screenshot=screenshot,
            format="json",
        )
    except Exception as e:
        return f"[error] Browser check failed: {e}"


def browser_test(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    start_command: str | None = None,
    port: int = 5173,
    startup_wait: int = 8,
    viewport: dict | None = None,
) -> str:
    """Backward-compatible wrapper for browser_check (interact mode).
    
    DEPRECATED: Use browser_check(mode='interact') directly.
    """
    if start_command:
        server_result = start_dev_server(start_command, port, startup_wait)
        if server_result.startswith("[error]"):
            return server_result
    return browser_check(
        url=url,
        mode="interact",
        actions=actions,
        screenshot=screenshot,
        viewport=viewport,
        wait=startup_wait,
    )


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
    {
        "type": "function",
        "function": {
            "name": "validate_build",
            "description": (
                "Explicitly run build validation (npm run build) and return the result. "
                "Use after writing multiple files to check if the project compiles. "
                "Also automatically triggered after write_file/edit_file with a 30s cooldown."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_init",
            "description": (
                "Initialize a new project by copying a pre-cached template. "
                "Much faster than running npm create / npx create-next-app. "
                "Available templates: 'vite-react-ts', 'nextjs-app'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "description": "Template name. Options: 'pure-html' (single file, no build), 'vite-react-ts', 'nextjs-app'.",
                        "enum": ["pure-html", "vite-react-ts", "nextjs-app"],
                    },
                },
                "required": ["template"],
            },
        },
    },
]

BROWSER_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browser_check",
            "description": (
                "Unified browser interaction tool. Replaces browser_test and browser_evaluate. "
                "Use mode='inspect' for DOM queries (like browser_evaluate). "
                "Use mode='interact' for action chains (like browser_test). "
                "Use mode='screenshot' for visual verification. "
                "Always set fresh=True when verifying recent code changes to avoid Vite HMR cache issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to. Default: http://localhost:5173",
                        "default": "http://localhost:5173",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Mode: 'inspect' (JS eval), 'interact' (action chain), 'screenshot' (visual).",
                        "enum": ["inspect", "interact", "screenshot"],
                        "default": "inspect",
                    },
                    "viewport": {
                        "type": "object",
                        "description": "Browser viewport. Default: {\"width\": 1280, \"height\": 720}.",
                        "properties": {
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "Clear all caches and force refresh. IMPORTANT: Set to True when verifying recent code changes to avoid stale Vite HMR cache.",
                        "default": False,
                    },
                    "wait": {
                        "type": "integer",
                        "description": "Seconds to wait after navigation. Default: 2.",
                        "default": 2,
                    },
                    "actions": {
                        "type": "array",
                        "description": "Action chain for mode='interact'. Each action: {type: 'click'|'fill'|'wait'|'scroll'|'evaluate'|'upload', selector?, value?, delay?, files?}. For 'upload': {type: 'upload', selector: 'input[type=file]', files: [{name: 'test.mp3', type: 'audio/mpeg', content: ''}]}",
                    },
                    "script": {
                        "type": "string",
                        "description": "JavaScript for mode='inspect'. Example: 'return document.querySelectorAll(\".card\").length'",
                    },
                    "screenshot": {
                        "type": "boolean",
                        "description": "Take screenshot. Default: false.",
                        "default": False,
                    },
                },
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
                "and health checks. Do NOT use `npm run dev &` directly."
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
    "browser_check": browser_check,
    "read_skill_file": read_skill_file,
    "generate_image": generate_image,
    "start_dev_server": start_dev_server,
    "search_web": search_web,
    "analyze_image": analyze_image,
    "validate_build": validate_build,
    "project_init": project_init,
}


def execute_tool(name: str, arguments: dict) -> str:

    """"""
    fn = TOOL_DISPATCH.get(name)

    if not fn:

        return f"[error] Unknown tool: {name}"

    try:

        return fn(**arguments)

    except Exception as e:

        return f"[error] {type(e).__name__}: {e}"



