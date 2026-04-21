"""
构建任务构建 + Dev Server 验证
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger("harness")


def build_build_task(
    workspace: Path,
    round_num: int,
    rollback_msg: str,
    overall_score_history: list[float],
    strategy_history: list[dict],
) -> str:
    """Build the task prompt for the Builder agent."""
    sprint_path = workspace / config.SPRINT_FILE
    sprint_contract_path = workspace / config.SPRINT_CONTRACT_FILE
    feedback_path = workspace / config.FEEDBACK_FILE

    # Primary guide: prefer sprint.md; fall back to spec.md
    if sprint_path.exists():
        primary_guide = f"1. Read {config.SPRINT_FILE} — this is your ONLY task list for this round.\n"
    else:
        primary_guide = f"1. Read {config.SPEC_FILE} for product spec.\n"

    # Use per-sprint contract when available; fall back to global contract
    if sprint_contract_path.exists():
        primary_guide += (
            f"2. Read {config.SPRINT_CONTRACT_FILE} — this is the Definition of Done for THIS round.\n"
            f"   (You may also read {config.CONTRACT_FILE} for broader context.)\n"
        )
    else:
        primary_guide += f"2. Read {config.CONTRACT_FILE} for acceptance criteria.\n"

    task = f"Round {round_num} of building.{rollback_msg}\n\nSteps:\n{primary_guide}"

    if feedback_path.exists() and round_num > 1:
        task += f"3. Read {config.FEEDBACK_FILE} for previous feedback and address relevant issues.\n"

        if len(overall_score_history) >= 2:
            delta = overall_score_history[-1] - overall_score_history[-2]
            if delta > 0:
                task += f"\nTrend: Improving (+{delta:.1f}), continue refining."
            elif delta < 0:
                task += f"\nTrend: Declining ({delta:.1f}), consider pivoting."
            else:
                task += f"\nTrend: Flat, try a different approach."

    # Inject the previous round's strategy decision
    if strategy_history:
        prev = strategy_history[-1]
        if prev["strategy"] == "PIVOT":
            task += (
                f"\n\nPREVIOUS ROUND STRATEGY: PIVOT"
                f"\nReason: {prev['reason']}"
            )
            if prev.get("new_direction"):
                task += (
                    f"\nNew direction declared: {prev['new_direction']}"
                    f"\n\nACTION REQUIRED: You declared a PIVOT. You MUST start from scratch with "
                    f"a fundamentally different approach as described above. Do NOT continue patching "
                    f"the previous implementation — delete or replace the core files and rebuild."
                )
        else:
            task += (
                f"\n\nPREVIOUS ROUND STRATEGY: REFINE"
                f"\nReason: {prev['reason']}"
                f"\nContinue improving the existing implementation."
            )

    task += "\n\nCommit with git when done."
    return task


def verify_dev_server(workspace: Path, port: int = None, max_wait: int = None) -> tuple[bool, str]:
    """Harness 层 dev server 验证。

    TODO: 当前仅通过 .workspace_state.json 的 build_status 判断。
    完整实现应启动 dev server 并轮询 HTTP 200 + 内容验证。

    Returns:
        (success, message)
    """
    port = port or config.DEV_SERVER_PORTS["nextjs"]
    max_wait = max_wait or config.DEV_SERVER_MAX_WAIT

    ws_state_path = workspace / ".workspace_state.json"
    if ws_state_path.exists():
        try:
            ws_data = json.loads(ws_state_path.read_text(encoding="utf-8"))
            if ws_data.get("last_build_status") == "error":
                return False, "Build status is error (from workspace state)"
            if ws_data.get("last_build_status") == "ok":
                return True, f"Build OK (port {port})"
        except Exception:
            pass
    return True, "No build status available, proceeding"
