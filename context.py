"""
上下文生命周期管理
"""
from __future__ import annotations

import re
import subprocess
import logging

import config

log = logging.getLogger("harness")

# 尝试使用 tiktoken，否则回退到字符估算
_encoder = None
_use_tiktoken = False

try:
    import tiktoken
    _use_tiktoken = True
except ImportError:
    pass


def _get_encoder():
    global _encoder
    if not _use_tiktoken:
        return None
    if _encoder is None:
        try:
            _encoder = tiktoken.encoding_for_model(config.MODEL)
        except Exception:
            _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(messages: list[dict]) -> int:
    """计算消息列表的 token 数量"""
    enc = _get_encoder()
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        text = str(content)
        if enc:
            total += len(enc.encode(text)) + 4
        else:
            total += len(text) // 4 + 4
        for tc in msg.get("tool_calls", []):
            args = str(tc.get("function", {}).get("arguments", ""))
            if enc:
                total += len(enc.encode(args))
            else:
                total += len(args) // 4
    return total


# 焦虑信号模式
_ANXIETY_PATTERNS = [
    r"(?i)let me wrap up",
    r"(?i)i('ll| will) finalize",
    r"(?i)that should be (enough|sufficient)",
    r"(?i)i('ll| will) stop here",
    r"(?i)due to (context |token )?limit",
    r"(?i)running (low on|out of) (context|space|tokens)",
    r"(?i)to (save|conserve) (context|space|tokens)",
    r"(?i)i('ve| have) covered the (main|key|essential)",
    r"(?i)in the interest of (time|space|brevity)",
]


def detect_anxiety(messages: list[dict]) -> bool:
    """检测上下文焦虑"""
    recent_texts = []
    for msg in reversed(messages[-10:]):
        if msg.get("role") == "assistant" and msg.get("content"):
            recent_texts.append(msg["content"])
        if len(recent_texts) >= 3:
            break

    combined = " ".join(recent_texts)
    matches = sum(1 for p in _ANXIETY_PATTERNS if re.search(p, combined))
    if matches >= 2:
        log.warning(f"Context anxiety detected ({matches} signals)")
        return True
    return False


def compact_messages(messages: list[dict], llm_call, role: str = "default") -> list[dict]:
    """压缩消息列表"""
    if not messages:
        return messages

    retention = {"evaluator": 0.50, "builder": 0.20}.get(role, 0.30)

    system = [messages[0]] if messages[0].get("role") == "system" else []
    non_system = messages[len(system):]

    keep_count = max(4, int(len(non_system) * retention))

    split_idx = len(non_system) - keep_count
    split_idx = _safe_split_index(non_system, split_idx)
    old = non_system[:split_idx]
    recent = non_system[split_idx:]

    if not old:
        return messages

    old_text = _messages_to_text(old)

    if role == "evaluator":
        instruction = (
            "Summarize the QA work log. Preserve: all scores given, "
            "bugs found, quality assessments, and cross-round comparisons. "
            "The evaluator needs this history to track improvement trends."
        )
    elif role == "builder":
        instruction = (
            "Summarize the following build log. Preserve: files created/modified, "
            "current architecture decisions, and the latest error states. "
            "Discard intermediate debugging steps and superseded code."
        )
    else:
        instruction = (
            "Summarize the following agent work log. Preserve: key decisions, "
            "files created/modified, current progress, and errors encountered."
        )

    summary = llm_call([
        {"role": "system", "content": f"You are a concise summarizer. {instruction}"},
        {"role": "user", "content": old_text},
    ])

    summary_msg = {
        "role": "user",
        "content": f"[COMPACTED CONTEXT — summary of earlier work]\n{summary}",
    }

    log.info(f"Context compacted (retained {len(recent)} messages)")
    return system + [summary_msg] + recent


def _safe_split_index(messages: list[dict], target_idx: int) -> int:
    """找到安全的分割点"""
    idx = max(0, min(target_idx, len(messages)))

    while idx > 0 and idx < len(messages):
        msg = messages[idx]
        if msg.get("role") == "tool":
            idx -= 1
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            idx -= 1
        else:
            break

    return idx


def create_checkpoint(messages: list[dict], llm_call) -> str:
    """创建 checkpoint 文档"""
    from pathlib import Path

    text = _messages_to_text(messages)
    checkpoint = llm_call([
        {"role": "system", "content": (
            "You are creating a handoff document for the next agent session. "
            "The next session starts with a COMPLETELY EMPTY context window — "
            "it has zero memory of anything that happened here.\n\n"
            "Structure the handoff as:\n"
            "## Completed Work\n(what was built, with file paths)\n"
            "## Current State\n(what works, what's broken right now)\n"
            "## Next Steps\n(exactly what to do next, in order)\n"
            "## Key Decisions & Rationale\n(why things were done this way)\n"
            "## Known Issues\n(bugs, incomplete features, technical debt)\n\n"
            "Be thorough and specific — file paths, function names, error messages. "
            "The next session's success depends entirely on this document."
        )},
        {"role": "user", "content": text},
    ])

    progress_path = Path(config.WORKSPACE) / config.PROGRESS_FILE
    progress_path.write_text(checkpoint, encoding="utf-8")
    log.info(f"Checkpoint written to {config.PROGRESS_FILE}")

    return checkpoint


def restore_from_checkpoint(checkpoint: str, system_prompt: str) -> list[dict]:
    """从 checkpoint 恢复"""
    git_context = ""
    try:
        result = subprocess.run(
            "git diff --stat HEAD~5 2>/dev/null || git log --oneline -5 2>/dev/null",
            shell=True, cwd=config.WORKSPACE, capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            git_context = f"\n\nRecent code changes:\n```\n{result.stdout.strip()[:2000]}\n```"
    except Exception:
        pass

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            "You are resuming an in-progress project. Your previous session's "
            "context was reset to give you a clean slate.\n\n"
            "Here is the handoff document from the previous session:\n\n"
            + checkpoint + git_context +
            "\n\nContinue from where the previous session left off. "
            "Do NOT redo work that's already completed."
        )},
    ]


def _messages_to_text(messages: list[dict]) -> str:
    """将消息列表转换为文本"""
    parts = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        if content:
            parts.append(f"[{role}] {content[:3000]}")
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            parts.append(f"[tool_call] {fn.get('name', '?')}({fn.get('arguments', '')[:500]})")
    return "\n".join(parts)
