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
        # Also increase for React/Vue components which can grow large with many features.
        if p.suffix in (".html", ".htm"):
            limit = 120_000
        elif p.suffix in (".tsx", ".ts", ".jsx", ".js"):
            limit = 80_000
        else:
            limit = 50_000

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
    """Kill process listening on a port."""
    if os.name == "nt":
        run_bash(
            f'for /f "tokens=5" %a in ("netstat -ano ^| findstr :{port}") do taskkill /F /PID %a 2>nul',
            timeout=10,
        )
    else:
        # Try fuser/lsof first, fallback to Python /proc scan
        run_bash(f"fuser -k {port}/tcp 2>/dev/null || lsof -ti:{port} | xargs kill -9 2>/dev/null || true", timeout=10)
        # Python fallback: scan /proc for processes with socket on this port
        try:
            import glob, struct
            port_hex = format(port, '04X')
            for tcp_file in glob.glob('/proc/[0-9]*/fd/[0-9]*'):
                try:
                    link = os.readlink(tcp_file)
                    if 'socket:' in link:
                        # Check if this process's cmdline contains vite/npm
                        pid = tcp_file.split('/')[2]
                        cmdline_path = f'/proc/{pid}/cmdline'
                        if os.path.exists(cmdline_path):
                            with open(cmdline_path, 'rb') as f:
                                cmd = f.read().decode('utf-8', 'replace')
                            if 'vite' in cmd or 'npm' in cmd:
                                os.kill(int(pid), signal.SIGTERM)
                except (OSError, ValueError, ProcessLookupError):
                    pass
        except Exception:
            pass

def _kill_dev_server() -> None:
    """Kill dev server and clear build caches to prevent stale content."""
    # FIX: Kill ALL common dev server ports to prevent multiple Vite processes
    # from competing for the same cache directory.
    for p in (3000, 5173, 5174, 5175, 5176, 5177, 5178, 5179, 5180,
              5181, 5182, 5183, 5184, 5185, 5186, 5187, 5188, 5189, 5190):
        _kill_port(p)
    # Also kill any process holding vite-related file locks (Python fallback for containers without pkill)
    if os.name != "nt":
        try:
            import glob
            for pid_dir in glob.glob('/proc/[0-9]*'):
                try:
                    pid = int(os.path.basename(pid_dir))
                    exe = os.readlink(f'{pid_dir}/exe')
                    if 'node' in exe:
                        with open(f'{pid_dir}/cmdline', 'rb') as f:
                            cmd = f.read().decode('utf-8', 'replace')
                        if 'vite' in cmd or ('npm' in cmd and 'dev' in cmd):
                            os.kill(pid, signal.SIGTERM)
                except (OSError, ValueError, ProcessLookupError):
                    pass
        except Exception:
            pass
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

def react_devtools_inspect(
    component_name: str,
    check_props: dict | None = None,
    check_state: dict | None = None,
) -> str:
    """Inspect React component tree using React DevTools protocol.
    
    DEPRECATED: This tool has event loop compatibility issues with the async Playwright MCP.
    Use browser_check with mode='inspect' and a custom script instead.
    
    Example replacement:
      browser_check(mode="inspect", script="return document.querySelector('[data-testid=\\'my-component\\']') !== null")
    """
    return (
        "[note] react_devtools_inspect is deprecated due to event loop conflicts with Playwright MCP.\n"
        "Please use browser_check with mode='inspect' and a custom script instead.\n"
        f"To check for '{component_name}', use:\n"
        "  browser_check(mode='inspect', script=\"return document.querySelector('[data-testid=\\\"...\\\"]') !== null\")"
    )


def contract_test_run(feature_group: str) -> str:
    """Run contract tests for the specified feature group.
    
    Analyzes source code statically to verify contract criteria without browser.
    Returns per-criterion scores and overall pass/fail status.
    """
    try:
        from harness.contract_tests import ContractTestRunner
        
        runner = ContractTestRunner(Path(config.WORKSPACE))
        result = runner.run_for_group(feature_group)
        
        return json.dumps({
            "feature_group": feature_group,
            "score": result["score"],
            "passed": result["passed"],
            "testable_criteria": result.get("testable_criteria", 0),
            "tests_run": result.get("tests_run", 0),
            "results": result["results"],
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Contract test failed: {e}"


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


def check_console_logs(
    url: str = "http://localhost:5173",
    level: str = "error",
    filter_keyword: str | None = None,
    fresh: bool = False,
    wait: int = 3,
) -> str:
    """获取浏览器控制台日志，用于检测 React 错误、网络失败、JS 异常等。
    
    Args:
        url: 目标 URL
        level: 日志级别 - "error"(默认), "warning", "all"
        filter_keyword: 可选过滤关键词（如 "Maximum update depth"）
        fresh: 是否强制刷新页面
        wait: 页面加载后等待秒数
    
    Returns:
        JSON 格式的日志列表
    """
    try:
        import asyncio
        from tools.playwright_mcp import _browser_check_async
        
        async def _get_logs():
            result = await _browser_check_async(
                url=url,
                mode="inspect",
                fresh=fresh,
                wait=wait,
                script="return 'ok'",  # 简单脚本确保页面加载
            )
            if isinstance(result, dict):
                return result.get("console_errors", [])
            return []
        
        # 运行异步函数
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_get_logs()))
                logs = future.result(timeout=60)
        except RuntimeError:
            logs = asyncio.run(_get_logs())
        
        # 过滤日志
        filtered = logs
        if level == "error":
            filtered = [log for log in logs if any(kw in log.lower() for kw in ["error", "exception", "failed"])]
        elif level == "warning":
            filtered = [log for log in logs if any(kw in log.lower() for kw in ["error", "exception", "failed", "warning", "warn"])]
        
        if filter_keyword:
            filtered = [log for log in filtered if filter_keyword.lower() in log.lower()]
        
        # 分类统计
        errors = [log for log in filtered if "error" in log.lower() or "exception" in log.lower()]
        warnings = [log for log in filtered if "warn" in log.lower() and log not in errors]
        
        return json.dumps({
            "total_logs": len(logs),
            "filtered_count": len(filtered),
            "errors": errors[:10],  # 最多返回 10 条
            "warnings": warnings[:5],
            "has_critical_errors": len(errors) > 0,
            "sample": filtered[:3] if filtered else [],
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Failed to get console logs: {e}"


def check_responsive(
    url: str = "http://localhost:5173",
    breakpoints: list[dict] | None = None,
    fresh: bool = True,
) -> str:
    """在多个视口尺寸下截图并检查布局问题。
    
    Args:
        url: 目标 URL
        breakpoints: 视口尺寸列表，默认 [mobile, tablet, desktop]
        fresh: 是否强制刷新（默认 True，避免缓存）
    
    Returns:
        JSON 格式的各尺寸截图结果和布局检查
    """
    try:
        import asyncio
        from tools.playwright_mcp import _browser_check_async
        
        default_breakpoints = [
            {"name": "mobile", "width": 375, "height": 667},
            {"name": "tablet", "width": 768, "height": 1024},
            {"name": "desktop", "width": 1280, "height": 720},
            {"name": "wide", "width": 1920, "height": 1080},
        ]
        bps = breakpoints or default_breakpoints
        
        async def _check_one(bp: dict) -> dict:
            viewport = {"width": bp["width"], "height": bp["height"]}
            result = await _browser_check_async(
                url=url,
                mode="screenshot",
                viewport=viewport,
                fresh=fresh,
                wait=3,
                screenshot=True,
            )
            if isinstance(result, dict):
                screenshot_file = result.get("screenshot", "")
                # 获取页面结构摘要
                inspect_result = await _browser_check_async(
                    url=url,
                    mode="inspect",
                    viewport=viewport,
                    fresh=False,
                    wait=2,
                    script="""
                        return {
                            scrollWidth: document.documentElement.scrollWidth,
                            scrollHeight: document.documentElement.scrollHeight,
                            viewportWidth: window.innerWidth,
                            viewportHeight: window.innerHeight,
                            hasHorizontalScroll: document.documentElement.scrollWidth > window.innerWidth,
                            bodyOverflow: getComputedStyle(document.body).overflow,
                            metaViewport: !!document.querySelector('meta[name="viewport"]'),
                        };
                    """,
                )
                inspect_data = inspect_result.get("script_result", {}) if isinstance(inspect_result, dict) else {}
                return {
                    "breakpoint": bp["name"],
                    "viewport": viewport,
                    "screenshot": screenshot_file,
                    "has_horizontal_scroll": inspect_data.get("hasHorizontalScroll", False),
                    "scroll_height": inspect_data.get("scrollHeight", 0),
                    "meta_viewport": inspect_data.get("metaViewport", False),
                    "status": "ok",
                }
            return {"breakpoint": bp["name"], "status": "error", "error": str(result)}
        
        async def _run_all():
            results = []
            for bp in bps:
                result = await _check_one(bp)
                results.append(result)
                await asyncio.sleep(0.5)
            return results
        
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_run_all()))
                all_results = future.result(timeout=120)
        except RuntimeError:
            all_results = asyncio.run(_run_all())
        
        # 分析结果
        issues = []
        for r in all_results:
            if r.get("has_horizontal_scroll"):
                issues.append(f"{r['breakpoint']}: Horizontal scroll detected (layout overflow)")
            if not r.get("meta_viewport") and r["breakpoint"] == "mobile":
                issues.append("mobile: Missing viewport meta tag")
        
        return json.dumps({
            "breakpoints_tested": len(all_results),
            "results": all_results,
            "issues": issues,
            "has_issues": len(issues) > 0,
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Responsive check failed: {e}"


def check_a11y(
    url: str = "http://localhost:5173",
    rules: list[str] | None = None,
    fresh: bool = False,
) -> str:
    """检查无障碍问题（基于 DOM 分析）。
    
    Args:
        url: 目标 URL
        rules: 检查规则列表，默认全部
        fresh: 是否强制刷新
    
    Returns:
        JSON 格式的无障碍检查结果
    """
    try:
        import asyncio
        from tools.playwright_mcp import _browser_check_async
        
        default_rules = ["alt", "labels", "contrast", "focus", "landmarks", "headings"]
        check_rules = rules or default_rules
        
        async def _run_check():
            # 运行全面的 DOM 无障碍检查脚本
            script = """
                (() => {
                    const results = {
                        images_without_alt: [],
                        inputs_without_labels: [],
                        buttons_without_text: [],
                        low_contrast_elements: [],
                        missing_focus_indicators: [],
                        missing_landmarks: [],
                        heading_issues: [],
                    };
                    
                    // 1. 图片 alt 检查
                    document.querySelectorAll('img').forEach(img => {
                        if (!img.alt && !img.getAttribute('aria-label')) {
                            results.images_without_alt.push({
                                src: img.src?.split('/').pop() || 'unknown',
                                tag: img.outerHTML?.substring(0, 100) || '<img>',
                            });
                        }
                    });
                    
                    // 2. 输入框 label 检查
                    document.querySelectorAll('input, select, textarea').forEach(el => {
                        const id = el.id;
                        const ariaLabel = el.getAttribute('aria-label');
                        const ariaLabelledBy = el.getAttribute('aria-labelledby');
                        const hasLabel = id && document.querySelector(`label[for="${id}"]`);
                        const hasPlaceholder = el.placeholder;
                        if (!hasLabel && !ariaLabel && !ariaLabelledBy && !hasPlaceholder) {
                            results.inputs_without_labels.push({
                                type: el.type || el.tagName,
                                id: id || 'no-id',
                            });
                        }
                    });
                    
                    // 3. 按钮文本检查
                    document.querySelectorAll('button').forEach(btn => {
                        const text = btn.textContent?.trim();
                        const ariaLabel = btn.getAttribute('aria-label');
                        if (!text && !ariaLabel) {
                            results.buttons_without_text.push({
                                class: btn.className?.substring(0, 50) || 'no-class',
                            });
                        }
                    });
                    
                    // 4. 焦点指示器检查（简化版）
                    const focusable = document.querySelectorAll('button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])');
                    focusable.forEach(el => {
                        const style = window.getComputedStyle(el);
                        const outline = style.outline;
                        if (outline === 'none' || outline === '0px') {
                            // 检查是否有替代的焦点样式
                            const hasFocusStyle = el.matches(':focus-visible') || 
                                getComputedStyle(document.documentElement).getPropertyValue('--focus-ring');
                            if (!hasFocusStyle) {
                                results.missing_focus_indicators.push({
                                    tag: el.tagName,
                                    class: el.className?.substring(0, 50) || '',
                                });
                            }
                        }
                    });
                    
                    // 5. Landmark 检查
                    const landmarks = document.querySelectorAll('main, nav, aside, header, footer, [role="main"], [role="navigation"]');
                    if (landmarks.length === 0) {
                        results.missing_landmarks.push("No semantic landmarks found");
                    }
                    
                    // 6. 标题层级检查
                    const h1s = document.querySelectorAll('h1');
                    if (h1s.length === 0) {
                        results.heading_issues.push("Missing h1 heading");
                    } else if (h1s.length > 1) {
                        results.heading_issues.push(`Multiple h1 headings (${h1s.length})`);
                    }
                    
                    // 检查标题层级跳跃
                    let lastLevel = 0;
                    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => {
                        const level = parseInt(h.tagName[1]);
                        if (level > lastLevel + 1) {
                            results.heading_issues.push(`Heading jump: h${lastLevel} to h${level}`);
                        }
                        lastLevel = level;
                    });
                    
                    return results;
                })()
            """
            
            result = await _browser_check_async(
                url=url,
                mode="inspect",
                fresh=fresh,
                wait=3,
                script=script,
            )
            return result.get("script_result", {}) if isinstance(result, dict) else {}
        
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_run_check()))
                a11y_data = future.result(timeout=60)
        except RuntimeError:
            a11y_data = asyncio.run(_run_check())
        
        # 根据请求的 rules 过滤结果
        summary = {}
        total_issues = 0
        
        if "alt" in check_rules:
            imgs = a11y_data.get("images_without_alt", [])
            summary["images_without_alt"] = {"count": len(imgs), "samples": imgs[:3]}
            total_issues += len(imgs)
        
        if "labels" in check_rules:
            inputs = a11y_data.get("inputs_without_labels", [])
            summary["inputs_without_labels"] = {"count": len(inputs), "samples": inputs[:3]}
            total_issues += len(inputs)
        
        if "focus" in check_rules:
            focus = a11y_data.get("missing_focus_indicators", [])
            summary["missing_focus_indicators"] = {"count": len(focus), "samples": focus[:3]}
            total_issues += len(focus)
        
        if "landmarks" in check_rules:
            landmarks = a11y_data.get("missing_landmarks", [])
            summary["missing_landmarks"] = {"count": len(landmarks), "issues": landmarks}
            total_issues += len(landmarks)
        
        if "headings" in check_rules:
            headings = a11y_data.get("heading_issues", [])
            summary["heading_issues"] = {"count": len(headings), "issues": headings}
            total_issues += len(headings)
        
        if "contrast" in check_rules:
            # 简化对比度检查（实际应计算颜色对比度）
            summary["contrast_check"] = "Manual review recommended for color contrast"
        
        return json.dumps({
            "rules_checked": check_rules,
            "total_issues": total_issues,
            "summary": summary,
            "status": "pass" if total_issues == 0 else "issues_found",
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Accessibility check failed: {e}"


def check_performance(
    url: str = "http://localhost:5173",
    metrics: list[str] | None = None,
    fresh: bool = True,
) -> str:
    """获取性能指标（通过 Performance API）。
    
    Args:
        url: 目标 URL
        metrics: 指标列表 ["lcp", "fid", "cls", "tti", "tbt", "fcp", "ttfb"]
        fresh: 是否强制刷新（默认 True，确保冷加载）
    
    Returns:
        JSON 格式的性能指标
    """
    try:
        import asyncio
        from tools.playwright_mcp import _browser_check_async
        
        default_metrics = ["fcp", "lcp", "tti", "cls", "tbt", "ttfb"]
        check_metrics = metrics or default_metrics
        
        async def _run_check():
            # 使用 Performance API 和 PerformanceObserver
            script = """
                (() => {
                    const perfData = performance.getEntriesByType('navigation')[0];
                    const paintEntries = performance.getEntriesByType('paint');
                    const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
                    const clsEntries = performance.getEntriesByType('layout-shift');
                    
                    const fcp = paintEntries.find(e => e.name === 'first-contentful-paint')?.startTime;
                    const lcp = lcpEntries.length > 0 ? lcpEntries[lcpEntries.length - 1].startTime : null;
                    
                    // 计算 CLS
                    let cls = 0;
                    clsEntries.forEach(entry => {
                        if (!entry.hadRecentInput) {
                            cls += entry.value;
                        }
                    });
                    
                    return {
                        // Navigation Timing
                        ttfb: perfData?.responseStart - perfData?.startTime,
                        fcp: fcp,
                        lcp: lcp,
                        domInteractive: perfData?.domInteractive,
                        domComplete: perfData?.domComplete,
                        loadComplete: perfData?.loadEventEnd,
                        
                        // 资源统计
                        resourceCount: performance.getEntriesByType('resource').length,
                        totalTransferSize: performance.getEntriesByType('resource').reduce((sum, r) => sum + (r.transferSize || 0), 0),
                        
                        // CLS
                        cls: cls,
                        
                        // 内存（如果可用）
                        memoryUsed: performance.memory?.usedJSHeapSize,
                        memoryTotal: performance.memory?.totalJSHeapSize,
                    };
                })()
            """
            
            result = await _browser_check_async(
                url=url,
                mode="inspect",
                fresh=fresh,
                wait=5,  # 等待更长以确保 LCP
                script=script,
            )
            return result.get("script_result", {}) if isinstance(result, dict) else {}
        
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_run_check()))
                perf_data = future.result(timeout=60)
        except RuntimeError:
            perf_data = asyncio.run(_run_check())
        
        # 评估指标
        evaluations = {}
        
        if "ttfb" in check_metrics and perf_data.get("ttfb") is not None:
            ttfb = perf_data["ttfb"]
            evaluations["ttfb"] = {
                "value": round(ttfb, 1),
                "unit": "ms",
                "rating": "good" if ttfb < 200 else "needs-improvement" if ttfb < 600 else "poor",
            }
        
        if "fcp" in check_metrics and perf_data.get("fcp") is not None:
            fcp = perf_data["fcp"]
            evaluations["fcp"] = {
                "value": round(fcp, 1),
                "unit": "ms",
                "rating": "good" if fcp < 1800 else "needs-improvement" if fcp < 3000 else "poor",
            }
        
        if "lcp" in check_metrics and perf_data.get("lcp") is not None:
            lcp = perf_data["lcp"]
            evaluations["lcp"] = {
                "value": round(lcp, 1),
                "unit": "ms",
                "rating": "good" if lcp < 2500 else "needs-improvement" if lcp < 4000 else "poor",
            }
        
        if "cls" in check_metrics and perf_data.get("cls") is not None:
            cls = perf_data["cls"]
            evaluations["cls"] = {
                "value": round(cls, 4),
                "unit": "",
                "rating": "good" if cls < 0.1 else "needs-improvement" if cls < 0.25 else "poor",
            }
        
        # 资源统计
        resource_info = {}
        if perf_data.get("resourceCount"):
            resource_info["resource_count"] = perf_data["resourceCount"]
        if perf_data.get("totalTransferSize"):
            resource_info["total_transfer_kb"] = round(perf_data["totalTransferSize"] / 1024, 1)
        
        return json.dumps({
            "metrics_requested": check_metrics,
            "evaluations": evaluations,
            "raw_data": {k: v for k, v in perf_data.items() if v is not None},
            "resource_info": resource_info,
            "overall_rating": _calculate_performance_rating(evaluations),
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Performance check failed: {e}"


def _calculate_performance_rating(evaluations: dict) -> str:
    """计算整体性能评级"""
    ratings = [e["rating"] for e in evaluations.values() if "rating" in e]
    if not ratings:
        return "unknown"
    if any(r == "poor" for r in ratings):
        return "poor"
    if any(r == "needs-improvement" for r in ratings):
        return "needs-improvement"
    return "good"


def check_routes(
    expected_routes: list[str] | None = None,
    base_url: str = "http://localhost:3000",
    check_404: bool = True,
) -> str:
    """验证 Next.js 路由可访问性。
    
    Args:
        expected_routes: 期望的路由列表，如 ["/", "/about", "/blog/[slug]"]
        base_url: 基础 URL
        check_404: 是否检查 404 页面
    
    Returns:
        JSON 格式的路由检查结果
    """
    try:
        import requests
        
        ws = Path(config.WORKSPACE)
        
        # 如果没有提供路由，自动从 app 目录发现
        if not expected_routes:
            app_dir = ws / "app"
            if app_dir.exists():
                routes = []
                for page_file in app_dir.rglob("page.tsx"):
                    rel_path = page_file.relative_to(app_dir)
                    route = "/" + str(rel_path.parent).replace("\\", "/").replace("page.tsx", "")
                    if route.endswith("/") and route != "/":
                        route = route[:-1]
                    routes.append(route)
                expected_routes = sorted(set(routes))
            else:
                return json.dumps({"error": "No app directory found and no routes provided"}, indent=2)
        
        results = []
        for route in expected_routes:
            url = f"{base_url.rstrip('/')}{route}"
            try:
                resp = requests.get(url, timeout=10, allow_redirects=False)
                status = resp.status_code
                
                result = {
                    "route": route,
                    "url": url,
                    "status": status,
                    "ok": status == 200,
                    "redirect": 300 <= status < 400,
                }
                
                if status == 200:
                    # 检查是否有实际内容（不是空白页）
                    has_content = len(resp.text) > 500 and "<body" in resp.text.lower()
                    result["has_content"] = has_content
                    if not has_content:
                        result["warning"] = "Page returned 200 but has minimal content"
                
                results.append(result)
                
            except requests.RequestException as e:
                results.append({
                    "route": route,
                    "url": url,
                    "status": "error",
                    "ok": False,
                    "error": str(e),
                })
        
        # 检查 404 页面
        not_found_result = None
        if check_404:
            try:
                not_found_url = f"{base_url.rstrip('/')}/__nonexistent_route_12345__"
                resp = requests.get(not_found_url, timeout=10)
                not_found_result = {
                    "route": "404",
                    "url": not_found_url,
                    "status": resp.status_code,
                    "has_custom_404": resp.status_code == 404 and len(resp.text) > 200,
                }
            except Exception as e:
                not_found_result = {"route": "404", "error": str(e)}
        
        ok_count = sum(1 for r in results if r.get("ok"))
        total = len(results)
        
        return json.dumps({
            "routes_tested": total,
            "routes_ok": ok_count,
            "routes_failed": total - ok_count,
            "pass_rate": round(ok_count / total, 2) if total > 0 else 0,
            "results": results,
            "not_found_page": not_found_result,
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Route check failed: {e}"


def mock_api(
    endpoint: str,
    response: dict | list | str,
    status_code: int = 200,
    method: str = "GET",
    persist: bool = True,
) -> str:
    """Mock API 响应（通过写入本地 JSON 文件供前端读取）。
    
    注意：这不是真正的请求拦截，而是创建一个本地 JSON 文件，
    前端代码需要配置为从本地文件加载数据（开发模式）。
    
    Args:
        endpoint: API 端点路径，如 "/api/users"
        response: Mock 响应数据
        status_code: HTTP 状态码
        method: HTTP 方法
        persist: 是否持久化到文件
    
    Returns:
        JSON 格式的 mock 配置信息
    """
    try:
        ws = Path(config.WORKSPACE)
        
        # 创建 mock 数据目录
        mock_dir = ws / "public" / "mock"
        mock_dir.mkdir(parents=True, exist_ok=True)
        
        # 将 endpoint 转换为文件名
        safe_name = endpoint.strip("/").replace("/", "_").replace("[", "").replace("]", "")
        mock_file = mock_dir / f"{safe_name}.json"
        
        mock_data = {
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "response": response,
            "timestamp": time.time(),
        }
        
        if persist:
            mock_file.write_text(
                json.dumps(mock_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        
        # 同时创建一个可直接 import 的 JS 文件
        js_file = mock_dir / f"{safe_name}.js"
        js_content = f"""// Auto-generated mock data for {endpoint}
export const mockData = {json.dumps(response, ensure_ascii=False, indent=2)};
export const mockStatus = {status_code};
"""
        js_file.write_text(js_content, encoding="utf-8")
        
        return json.dumps({
            "status": "ok",
            "endpoint": endpoint,
            "mock_file": str(mock_file.relative_to(ws)),
            "js_file": str(js_file.relative_to(ws)),
            "usage": f"Import from '/mock/{safe_name}.js' or fetch '/mock/{safe_name}.json'",
            "note": "For dev mode: configure your fetch to use local mock files when API is unavailable",
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Mock API setup failed: {e}"


def detect_framework(workspace: str = ".") -> str:
    """自动检测项目使用的框架和技术栈。
    
    Args:
        workspace: 项目目录路径（默认当前目录）
    
    Returns:
        JSON 格式的检测结果
    """
    try:
        ws = Path(workspace).resolve()
        if not ws.exists():
            ws = Path(config.WORKSPACE).resolve()
        
        result = {
            "framework": "unknown",
            "has_package_json": False,
            "has_react": False,
            "has_vue": False,
            "has_nextjs": False,
            "has_vite": False,
            "test_url": "http://localhost:5173",
            "build_command": None,
            "dev_command": None,
        }
        
        # 检查 package.json
        pkg_json = ws / "package.json"
        if pkg_json.exists():
            result["has_package_json"] = True
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                scripts = pkg.get("scripts", {})
                
                # 检测框架
                if "next" in deps:
                    result["framework"] = "nextjs"
                    result["has_nextjs"] = True
                    result["test_url"] = "http://localhost:3000"
                    result["build_command"] = "next build"
                    result["dev_command"] = "npm run dev"
                elif "react" in deps or "react-dom" in deps:
                    result["has_react"] = True
                    if "vite" in deps or "@vitejs/plugin-react" in deps:
                        result["framework"] = "vite-react"
                        result["has_vite"] = True
                        result["test_url"] = "http://localhost:5173"
                        result["build_command"] = "npm run build"
                        result["dev_command"] = "npm run dev"
                    else:
                        result["framework"] = "react"
                        result["build_command"] = "npm run build"
                elif "vue" in deps:
                    result["has_vue"] = True
                    if "vite" in deps or "@vitejs/plugin-vue" in deps:
                        result["framework"] = "vite-vue"
                        result["has_vite"] = True
                        result["test_url"] = "http://localhost:5173"
                    else:
                        result["framework"] = "vue"
                    result["build_command"] = "npm run build"
                    result["dev_command"] = "npm run dev"
                elif "vite" in deps:
                    result["framework"] = "vite"
                    result["has_vite"] = True
                    result["test_url"] = "http://localhost:5173"
                    result["dev_command"] = "npm run dev"
                
                # 从 scripts 中提取命令
                if not result["dev_command"] and "dev" in scripts:
                    result["dev_command"] = scripts["dev"]
                if not result["build_command"] and "build" in scripts:
                    result["build_command"] = scripts["build"]
                    
            except Exception:
                pass
        
        # 检查源码中的框架线索
        if result["framework"] == "unknown":
            for src_file in ws.rglob("*.tsx"):
                if src_file.stat().st_size < 100000:
                    content = src_file.read_text(encoding="utf-8", errors="replace")
                    if "from 'react'" in content or 'from "react"' in content:
                        result["has_react"] = True
                        result["framework"] = "react"
                        break
            
            for src_file in ws.rglob("*.vue"):
                result["has_vue"] = True
                result["framework"] = "vue"
                break
        
        # 纯 HTML 项目
        if result["framework"] == "unknown":
            index_html = ws / "index.html"
            if index_html.exists() and not pkg_json.exists():
                result["framework"] = "pure-html"
                result["test_url"] = f"file://{index_html}"
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return f"[error] Framework detection failed: {e}"


def run_diagnostics(command: str = "build", workspace: str = ".") -> str:
    """运行安全的诊断命令（只读/构建检查，不修改文件）。
    
    Args:
        command: 诊断类型 - "build", "lint", "type-check"
        workspace: 项目目录路径
    
    Returns:
        诊断结果
    """
    try:
        ws = Path(workspace).resolve()
        if not ws.exists():
            ws = Path(config.WORKSPACE).resolve()
        
        # 白名单检查
        allowed_commands = {
            "build": "npm run build",
            "lint": "npm run lint",
            "type-check": "npx tsc --noEmit",
            "typecheck": "npx tsc --noEmit",
        }
        
        if command not in allowed_commands:
            return f"[error] Unknown diagnostic command: {command}. Allowed: {list(allowed_commands.keys())}"
        
        cmd = allowed_commands[command]
        
        # 运行命令
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        output = result.stdout + "\n" + result.stderr
        
        # 截断长输出
        if len(output) > 5000:
            output = output[:2500] + "\n... (truncated) ...\n" + output[-2500:]
        
        return json.dumps({
            "command": cmd,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
            "output": output.strip(),
        }, indent=2, ensure_ascii=False)
        
    except subprocess.TimeoutExpired:
        return json.dumps({
            "command": command,
            "success": False,
            "error": "Command timed out after 300s",
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"[error] Diagnostic failed: {e}"


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

CONTRACT_TEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "contract_test_run",
        "description": (
            "Run static contract tests for a feature group. Analyzes source code to verify "
            "contract criteria without browser automation. Use this BEFORE browser_check to "
            "quickly verify code correctness. Returns per-criterion scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "feature_group": {
                    "type": "string",
                    "description": "Feature group ID, e.g. 'F6', 'F7'",
                },
            },
            "required": ["feature_group"],
        },
    },
}

REACT_DEVTOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "react_devtools_inspect",
        "description": (
            "Inspect React component tree via DevTools protocol. Bypasses DOM timing issues "
            "by checking the React Fiber tree directly. Use when browser_check cannot find "
            "dynamically rendered components (e.g., cursors, animations)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "component_name": {
                    "type": "string",
                    "description": "React component name to find (e.g., 'CursorElement', 'Cursors')",
                },
                "check_props": {
                    "type": "object",
                    "description": "Expected props (optional). Example: {visible: true}",
                },
                "check_state": {
                    "type": "object",
                    "description": "Expected state (optional)",
                },
            },
            "required": ["component_name"],
        },
    },
}

BROWSER_TOOL_SCHEMAS = [
    CONTRACT_TEST_SCHEMA,
    REACT_DEVTOOLS_SCHEMA,
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
    {
        "type": "function",
        "function": {
            "name": "check_console_logs",
            "description": (
                "Get browser console logs (errors, warnings). Use to detect React errors, "
                "network failures, JS exceptions, infinite loops. Run this BEFORE detailed testing "
                "to catch critical issues early."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL. Default: http://localhost:5173",
                        "default": "http://localhost:5173",
                    },
                    "level": {
                        "type": "string",
                        "description": "Log level filter: 'error' (default), 'warning', 'all'",
                        "enum": ["error", "warning", "all"],
                        "default": "error",
                    },
                    "filter_keyword": {
                        "type": "string",
                        "description": "Optional filter keyword (e.g., 'Maximum update depth')",
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "Force refresh page before checking logs",
                        "default": False,
                    },
                    "wait": {
                        "type": "integer",
                        "description": "Seconds to wait after navigation",
                        "default": 3,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_framework",
            "description": (
                "Auto-detect project framework and tech stack. Use at the start of review "
                "to determine appropriate testing strategy (React/Vue/Next.js/HTML)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                        "description": "Project directory path. Default: current workspace",
                        "default": ".",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_diagnostics",
            "description": (
                "Run safe diagnostic commands (read-only/build check). Use to verify "
                "build success, type checking, or linting. Does NOT modify files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Diagnostic type: 'build', 'lint', 'type-check'",
                        "enum": ["build", "lint", "type-check"],
                        "default": "build",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Project directory path",
                        "default": ".",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_responsive",
            "description": (
                "Test responsive layout across multiple viewport sizes (mobile, tablet, desktop, wide). "
                "Takes screenshots at each breakpoint and checks for horizontal scroll (layout overflow). "
                "Use to verify the app looks correct on all device sizes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL. Default: http://localhost:5173",
                        "default": "http://localhost:5173",
                    },
                    "breakpoints": {
                        "type": "array",
                        "description": "Custom breakpoints. Default: mobile(375x667), tablet(768x1024), desktop(1280x720), wide(1920x1080)",
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "Force refresh. Default: true",
                        "default": True,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_a11y",
            "description": (
                "Check accessibility issues: missing alt text, unlabeled inputs, buttons without text, "
                "missing focus indicators, no semantic landmarks, heading hierarchy problems. "
                "Use to ensure the app meets basic accessibility standards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL. Default: http://localhost:5173",
                        "default": "http://localhost:5173",
                    },
                    "rules": {
                        "type": "array",
                        "description": "Rules to check: 'alt', 'labels', 'contrast', 'focus', 'landmarks', 'headings'. Default: all",
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "Force refresh. Default: false",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_performance",
            "description": (
                "Measure Core Web Vitals and performance metrics: FCP, LCP, CLS, TTFB, TTI. "
                "Uses browser Performance API. Best run with fresh=true for cold load measurements. "
                "Use to verify the app loads fast and doesn't have layout shift issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL. Default: http://localhost:5173",
                        "default": "http://localhost:5173",
                    },
                    "metrics": {
                        "type": "array",
                        "description": "Metrics to measure: 'fcp', 'lcp', 'cls', 'tti', 'tbt', 'ttfb'. Default: all",
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "Force refresh for cold load. Default: true",
                        "default": True,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_routes",
            "description": (
                "Verify Next.js routes are accessible. Auto-discovers routes from app/ directory "
                "or checks provided route list. Reports HTTP status, content presence, and 404 handling. "
                "Use for Next.js projects to ensure all pages work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expected_routes": {
                        "type": "array",
                        "description": "Expected routes like ['/', '/about', '/blog']. Auto-detected from app/ if not provided.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Base URL. Default: http://localhost:3000",
                        "default": "http://localhost:3000",
                    },
                    "check_404": {
                        "type": "boolean",
                        "description": "Check custom 404 page. Default: true",
                        "default": True,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mock_api",
            "description": (
                "Create mock API response files for frontend development. Writes JSON/JS files to public/mock/ "
                "that can be imported or fetched. Use when the app needs backend data but no API is available. "
                "NOT a real request interceptor — frontend code must be configured to use local files in dev mode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "endpoint": {
                        "type": "string",
                        "description": "API endpoint path, e.g., '/api/users'",
                    },
                    "response": {
                        "type": "object",
                        "description": "Mock response data (JSON object or array)",
                    },
                    "status_code": {
                        "type": "integer",
                        "description": "HTTP status code. Default: 200",
                        "default": 200,
                    },
                    "method": {
                        "type": "string",
                        "description": "HTTP method. Default: GET",
                        "default": "GET",
                    },
                    "persist": {
                        "type": "boolean",
                        "description": "Save to file. Default: true",
                        "default": True,
                    },
                },
                "required": ["endpoint", "response"],
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
    "contract_test_run": contract_test_run,
    "react_devtools_inspect": react_devtools_inspect,
    "check_console_logs": check_console_logs,
    "detect_framework": detect_framework,
    "run_diagnostics": run_diagnostics,
    "check_responsive": check_responsive,
    "check_a11y": check_a11y,
    "check_performance": check_performance,
    "check_routes": check_routes,
    "mock_api": mock_api,
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



