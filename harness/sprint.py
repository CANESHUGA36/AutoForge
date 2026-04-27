"""
Sprint 规划 — SprintMaster 直接产出 sprint.md（自带验收标准）
"""
from __future__ import annotations
import logging
from pathlib import Path
import config
from agents import Agent
log = logging.getLogger("harness")


def plan_sprint_master(
    workspace: Path,
    round_num: int,
    sprint_master: Agent,
    log: logging.Logger,
    task_limit: int = 2,
    budget_hint: str = "",
    group_hint: str = "",
) -> bool:
    """由 SprintMaster Agent 直接产出 sprint.md（自带验收标准）

    Args:
        task_limit: 本轮任务数量上限（旧版动态计算，功能组模式下固定为 1）。
        budget_hint: 额外的预算提示信息（如上轮实际迭代数）。
        group_hint: 当前功能组信息（功能组模式下由 Harness 注入，如 "F3 Waveform Visualization"）。

    Returns:
        True if sprint.md was created successfully, False otherwise.
    """
    log.info("Sprint planning phase")

    # Feature-group mode takes precedence
    if group_hint:
        prompt = f"""Plan sprint {round_num}.
Read spec.md and contract.md, list existing files, then write sprint.md.

## 当前功能组（由 Harness 指定，不可更改）
{group_hint}

## 你的任务
1. 从 contract.md 中只提取当前功能组对应的验收标准
2. 写入 sprint.md，严格对应这个功能组
3. 不要包含其他功能组的内容

{budget_hint}
"""
    else:
        # Legacy mode: dynamic task limit
        task_limit_msg = f"\n本轮任务上限：{task_limit} 个任务。"
        if task_limit >= 3:
            task_limit_msg += " Builder 近期表现良好，可以安排功能套件（Bundle）任务。"
        elif task_limit == 1:
            task_limit_msg += " 上轮表现不佳或超时，本轮聚焦一个核心功能。"

        prompt = f"""Plan sprint {round_num}.
Read spec.md and contract.md, list existing files, then write sprint.md.
{task_limit_msg}
{budget_hint}
"""

    # FIX BUG #10: Check return value and handle failure
    result = sprint_master.run(prompt)
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        log.warning("SprintMaster did not create sprint.md")
        # Try to write a fallback sprint.md so Builder isn't blocked
        try:
            fallback = f"""# Sprint {round_num} (Fallback)

## Goal
Continue building the project based on spec.md and contract.md.

## Tasks
- [ ] Review current implementation
- [ ] Fix any known issues
- [ ] Add next feature from spec

## Estimated Iterations
- 保守：15 次
- 乐观：10 次
"""
            sprint_path.write_text(fallback, encoding="utf-8")
            log.info("[sprint] Wrote fallback sprint.md")
        except Exception as e:
            log.error(f"[sprint] Failed to write fallback sprint.md: {e}")
        return False
    return True
