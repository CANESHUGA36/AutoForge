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


def _detect_project_port(workspace: Path) -> int:
    """Detect dev server port from package.json dependencies."""
    package_json = workspace / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "vite" in deps:
                return config.DEV_SERVER_PORTS["vite"]
            if "next" in deps:
                return config.DEV_SERVER_PORTS["nextjs"]
        except Exception:
            pass
    return config.DEV_SERVER_PORTS["nextjs"]


def verify_dev_server(workspace: Path, port: int = None, max_wait: int = None) -> tuple[bool, str]:
    """Harness-level dev server verification.

    First checks .workspace_state.json for build errors, then performs
    an actual HTTP health check against localhost:{port}.

    Returns:
        (success, message)
    """
    import urllib.request
    import time as _time

    port = port or _detect_project_port(workspace)
    max_wait = max_wait or config.DEV_SERVER_MAX_WAIT

    # Step 1: Check workspace state for known build errors
    ws_state_path = workspace / ".workspace_state.json"
    if ws_state_path.exists():
        try:
            ws_data = json.loads(ws_state_path.read_text(encoding="utf-8"))
            if ws_data.get("last_build_status") == "error":
                return False, "Build status is error (from workspace state)"
        except Exception:
            pass

    # Step 2: Actual HTTP health check with polling
    url = f"http://localhost:{port}"
    start = _time.time()
    while _time.time() - start < max_wait:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True, f"Dev server responding on port {port} (HTTP 200)"
                elif resp.status >= 500:
                    return False, f"Dev server error on port {port} (HTTP {resp.status})"
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                return False, f"Dev server error on port {port} (HTTP {e.code})"
            # 404 or other client errors might mean server is starting
        except Exception:
            pass
        _time.sleep(1)

    return False, f"Dev server not responding on port {port} after {max_wait}s"
