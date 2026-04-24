"""
Sprint 规划 — SprintMaster 直接产出 sprint.md（自带验收标准）
"""
from __future__ import annotations
import logging
from pathlib import Path
import config
from agents import Agent
log = logging.getLogger("harness")


def plan_sprint_master(workspace: Path, round_num: int, sprint_master: Agent, log: logging.Logger) -> None:
    """由 SprintMaster Agent 直接产出 sprint.md（自带验收标准）"""
    log.info("Sprint planning phase")
    task = f"""Plan sprint {round_num}.
Read spec.md and contract.md, list existing files, then write sprint.md.
"""
    sprint_master.run(task)
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        log.warning("SprintMaster did not create sprint.md")
