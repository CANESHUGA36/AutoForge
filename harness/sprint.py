"""
Sprint 规划 — SprintMaster 直接产出 sprint.md（自带验收标准）
"""
from __future__ import annotations
import logging
from pathlib import Path
import config
from agents import Agent
log = logging.getLogger("harness")


def plan_sprint_master(workspace: Path, round_num: int, sprint_master: Agent, log: logging.Logger) -> bool:
    """由 SprintMaster Agent 直接产出 sprint.md（自带验收标准）
    
    Returns:
        True if sprint.md was created successfully, False otherwise.
    """
    log.info("Sprint planning phase")
    task = f"""Plan sprint {round_num}.
Read spec.md and contract.md, list existing files, then write sprint.md.
"""
    # FIX BUG #10: Check return value and handle failure
    result = sprint_master.run(task)
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
