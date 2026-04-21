"""
CLI 入口
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import config
from harness.core import Harness


def _make_workspace(prompt: str) -> str:
    """根据 prompt 和时间戳自动生成独立的工作目录路径。"""
    slug = re.sub(r'[^\w\s-]', '', prompt.lower())
    slug = re.sub(r'\s+', '-', slug.strip())[:30].rstrip('-')
    ts = time.strftime("%Y%m%d-%H%M%S")
    return str(Path(config.PROJECTS_DIR) / f"{slug}-{ts}")


def main():
    parser = argparse.ArgumentParser(
        description="Harness - Multi-agent development",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Interrupt recovery:\n"
            "  If a run is interrupted, re-run with the same --workspace path.\n"
            "  The harness auto-detects harness_state.json and resumes from the\n"
            "  last completed round without repeating Phase 1 or Phase 2.\n\n"
            "  To force a fresh start on an existing workspace, delete\n"
            "  harness_state.json (and optionally spec.md / contract.md) first."
        ),
    )
    parser.add_argument("prompt", nargs="?", default="Build a Pomodoro timer with start, pause, reset buttons")
    parser.add_argument("--workspace", default=None,
                        help="工作目录路径。不指定时在 PROJECTS_DIR（默认 ./projects，Docker 为 /projects）下生成 <slug>-<timestamp>/")
    parser.add_argument("--reset", action="store_true",
                        help="Delete harness_state.json before starting, forcing a clean run "
                             "even if the workspace already contains a previous state file.")
    parser.add_argument("--dashboard", action="store_true",
                        help="Print dashboard state and exit (for monitoring a running harness).")
    args = parser.parse_args()

    # --dashboard: 只打印状态然后退出
    if args.dashboard and args.workspace:
        from dashboard import print_dashboard
        print_dashboard(args.workspace)
        return 0

    # 未手动指定 --workspace 时，自动生成带时间戳的独立目录
    workspace = args.workspace if args.workspace else _make_workspace(args.prompt)
    print(f"Workspace: {workspace}")

    # --reset: wipe the state file so the run starts from scratch
    if args.reset:
        state_path = Path(workspace) / config.STATE_FILE
        if state_path.exists():
            state_path.unlink()
            print(f"[state] --reset: deleted {config.STATE_FILE}")

    harness = Harness(workspace)
    result = harness.run(args.prompt)

    harness.log.info("\n" + "="*60)
    harness.log.info("Final Result")
    harness.log.info("="*60)
    harness.log.info(f"Success: {result['success']}")
    harness.log.info(f"Score: {result.get('score', 0)}")
    harness.log.info(f"Rounds: {result.get('rounds', 0)}")
    if "token_totals" in result:
        t = result["token_totals"]
        harness.log.info(f"Total tokens: {t.get('prompt', 0)} prompt + {t.get('completion', 0)} completion")
    if "error" in result:
        harness.log.error(f"Error: {result['error']}")

    return 0 if result['success'] else 1


if __name__ == "__main__":
    raise SystemExit(main())
