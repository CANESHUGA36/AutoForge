"""
工具实现
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

import config
from skills import get_skill_path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


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


def write_file(path: str, content: str) -> str:
    """写入文件"""
    try:
        if not path or not path.strip():
            return "[error] Empty file path"
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
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
        return f"Edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars"
    except Exception as e:
        return f"[error] {e}"


def list_files(directory: str = ".") -> str:
    """列出文件"""
    try:
        p = _resolve(directory)
        if not p.is_dir():
            return f"[error] Not a directory: {directory}"
        entries = []
        for item in sorted(p.rglob("*")):
            if item.is_file():
                rel = item.relative_to(Path(config.WORKSPACE).resolve())
                entries.append(str(rel))
        if not entries:
            return "(empty)"
        return "\n".join(entries[:200])
    except Exception as e:
        return f"[error] {e}"


def run_bash(command: str, timeout: int = 900) -> str:
    """执行 bash 命令"""
    timeout = max(timeout, 900)  # enforce a minimum of 900s to handle slow npm/package installs
    try:
        result = subprocess.run(
            command, shell=True, cwd=config.WORKSPACE,
            capture_output=True, text=True, timeout=timeout,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        output = _smart_truncate(result.stdout, result.stderr)
        if result.returncode != 0:
            output = f"[exit code: {result.returncode}]\n{output}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout}s"
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

        url = "https://api.minimax.io/v1/image_generation"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "image-01",
            "prompt": prompt.strip(),
            "aspect_ratio": aspect_ratio,
            "response_format": "base64",
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

        inner = data.get("data") or {}
        b64_list = inner.get("image_base64")
        if not b64_list or not isinstance(b64_list, list):
            return f"[error] Unexpected API response: {str(data)[:1200]}"

        raw = base64.b64decode(b64_list[0])
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


# Playwright 浏览器测试
_dev_server_proc = None


def browser_test(
    url: str,
    actions: list | None = None,
    screenshot: bool = True,
    start_command: str | None = None,
    port: int = 5173,
    startup_wait: int = 8,
    viewport: dict | None = None,
) -> str:
    """Browser test with configurable viewport. Default viewport is 1280x720 (desktop).
    Pass viewport={"width": 375, "height": 812} for mobile testing."""
    if not HAS_PLAYWRIGHT:
        return "[error] Playwright not installed"

    vp = viewport if isinstance(viewport, dict) else {"width": 1280, "height": 720}
    report_lines = [f"Viewport: {vp['width']}x{vp['height']}"]

    if start_command:
        global _dev_server_proc
        if _dev_server_proc is None or _dev_server_proc.poll() is not None:
            _dev_server_proc = subprocess.Popen(
                start_command, shell=True, cwd=config.WORKSPACE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            time.sleep(startup_wait)
        report_lines.append(f"Server started")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
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
    if len(result) > 10_000:
        result = result[:10_000] + "\n...[TRUNCATED: browser test report exceeded 10K chars]"
    return result


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
            "description": "Execute a bash command",
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
                    "start_command": {"type": "string"},
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
]

TOOL_DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "run_bash": run_bash,
    "browser_test": browser_test,
    "read_skill_file": read_skill_file,
    "generate_image": generate_image,
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
