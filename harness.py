#!/usr/bin/env python3
"""
Harness — 多 Agent 长时间自主开发架构（生产级）

基于 Anthropic 文章 "Harness design for long-running application development"
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import config
from agents import Agent
from prompts import (
    PLANNER_SYSTEM, BUILDER_SYSTEM, EVALUATOR_SYSTEM,
    CONTRACT_BUILDER_SYSTEM, CONTRACT_REVIEWER_SYSTEM
)
from tools import TOOL_SCHEMAS, BROWSER_TOOL_SCHEMAS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("harness")


class Harness:
    """Harness 编排器"""

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._init_git()

        # 创建三个 Agent
        self.planner = Agent("Planner", PLANNER_SYSTEM, TOOL_SCHEMAS)
        self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS)
        self.evaluator = Agent("Evaluator", EVALUATOR_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS)

        self.score_history: list[float] = []
        # Parallel list: commit_history[i] == (round_num, hash) for score_history[i]
        self.commit_history: list[tuple[int, str]] = []

    def _init_git(self):
        """初始化 git"""
        git_dir = self.workspace / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "config", "user.email", "harness@example.com"],
                          cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness"],
                          cwd=self.workspace, capture_output=True)

    def run(self, user_prompt: str) -> dict:
        """运行完整流程"""
        log.info("="*60)
        log.info("Harness starting")
        log.info("="*60)
        log.info(f"Workspace: {self.workspace}")
        log.info(f"Prompt: {user_prompt}")

        # Phase 1: Plan
        log.info("\n" + "="*60)
        log.info("Phase 1: Plan")
        log.info("="*60)

        self.planner.run(
            f"Create a product specification for:\n\n{user_prompt}\n\n"
            f"Save to {config.SPEC_FILE}"
        )

        spec_path = self.workspace / config.SPEC_FILE
        if not spec_path.exists():
            return {"success": False, "error": "Planner failed to create spec"}

        log.info(f"Spec created successfully")

        # Phase 2: Contract
        log.info("\n" + "="*60)
        log.info("Phase 2: Contract")
        log.info("="*60)
        self._negotiate_contract()

        # Phase 3+: Build-Evaluate loop
        for round_num in range(1, config.MAX_ROUNDS + 1):
            log.info("\n" + "="*60)
            log.info(f"Round {round_num}/{config.MAX_ROUNDS}")
            log.info("="*60)

            score = self._build_round(round_num)
            self.score_history.append(score)

            if score >= config.PASS_THRESHOLD:
                log.info(f"\n🎉 Success! Final score: {score}")
                return {"success": True, "score": score, "rounds": round_num}

            if round_num < config.MAX_ROUNDS:
                log.info(f"Score {score} below threshold {config.PASS_THRESHOLD}, continuing...")

        return {"success": False, "score": self.score_history[-1] if self.score_history else 0,
                "rounds": len(self.score_history)}

    def _negotiate_contract(self):
        """协商验收标准"""
        proposer = Agent("ContractProposer", CONTRACT_BUILDER_SYSTEM, TOOL_SCHEMAS)
        reviewer = Agent("ContractReviewer", CONTRACT_REVIEWER_SYSTEM, TOOL_SCHEMAS)

        for attempt in range(3):
            log.info(f"Contract negotiation attempt {attempt + 1}/3")

            proposer.run(f"Read {config.SPEC_FILE} and create acceptance criteria in {config.CONTRACT_FILE}")
            review = reviewer.run(f"Review {config.CONTRACT_FILE}")

            if "APPROVED" in review:
                log.info("Contract approved")
                return
            log.debug(f"Contract review: {review[:200]}...")

        log.info("Max contract negotiation attempts reached")

    def _get_head_hash(self) -> str | None:
        """Return the current HEAD commit hash, or None if repo has no commits yet."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace, capture_output=True, text=True
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _commit_round(self, round_num: int) -> str | None:
        """Guarantee a git snapshot after each build round, regardless of whether
        the Builder remembered to commit.

        Returns the HEAD commit hash after the operation (None if git has no commits)."""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace, capture_output=True, text=True
        )
        if not result.stdout.strip():
            log.info(f"[git] Nothing to commit after round {round_num}")
        else:
            subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
            msg = f"harness: round {round_num} snapshot"
            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.workspace, capture_output=True, text=True
            )
            if commit.returncode == 0:
                log.info(f"[git] Committed round {round_num} snapshot")
            else:
                log.warning(f"[git] Commit failed: {commit.stderr.strip()}")
        return self._get_head_hash()

    def _rollback_to(self, commit_hash: str, reason: str) -> None:
        """Hard-reset workspace to a specific commit and log why."""
        log.info(f"[git] Rolling back to {commit_hash[:8]} — {reason}")
        result = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            cwd=self.workspace, capture_output=True, text=True
        )
        if result.returncode == 0:
            log.info("[git] Rollback successful")
        else:
            log.warning(f"[git] Rollback failed: {result.stderr.strip()}")

    def _build_round(self, round_num: int) -> float:
        """执行一轮 Build-Evaluate"""
        # Rollback check: if the last round's score is below the historical best,
        # reset the workspace to the best-known commit before the Builder starts.
        rollback_msg = ""
        if self.score_history and self.commit_history:
            best_idx = max(range(len(self.score_history)), key=lambda i: self.score_history[i])
            best_score = self.score_history[best_idx]
            last_score = self.score_history[-1]
            if last_score < best_score:
                _, best_hash = self.commit_history[best_idx]
                self._rollback_to(
                    best_hash,
                    f"score dropped {last_score:.1f} → best was {best_score:.1f} at round {best_idx + 1}"
                )
                rollback_msg = (
                    f"\nNOTE: The workspace was rolled back to round {best_idx + 1} "
                    f"(score {best_score:.1f}) because the last round regressed to {last_score:.1f}. "
                    f"Build from this better baseline."
                )

        # Build
        log.info("Build phase")
        build_task = self._build_build_task(round_num, rollback_msg)
        self.builder.run(build_task)

        # Harness-level git snapshot — ensures history even if Builder skipped commit
        head_hash = self._commit_round(round_num)
        if head_hash:
            self.commit_history.append((round_num, head_hash))

        # Evaluate
        log.info("Evaluate phase")
        eval_task = f"""
Evaluate the current code against acceptance criteria.

1. Read {config.CONTRACT_FILE} for criteria
2. Examine code files in the workspace
3. If it's a web app, use run_bash to start the dev server (e.g. `npm run dev &`),
   then call browser_test with url="http://localhost:5173" and relevant actions
   to verify each functional criterion
4. Give a score and detailed feedback
5. Save feedback to {config.FEEDBACK_FILE}

Include "SCORE: X/10" in your feedback.
"""
        eval_result = self.evaluator.run(eval_task)
        score = self._parse_score(eval_result)
        log.info(f"Round score: {score}/10")

        return score

    def _build_build_task(self, round_num: int, rollback_msg: str = "") -> str:
        """构建 Builder 任务"""
        task = f"""Round {round_num} of building.{rollback_msg}

Steps:
1. Read {config.SPEC_FILE} for product spec
2. Read {config.CONTRACT_FILE} for acceptance criteria
"""
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists() and round_num > 1:
            task += f"3. Read {config.FEEDBACK_FILE} for previous feedback\n4. Address all issues\n"

            if len(self.score_history) >= 2:
                delta = self.score_history[-1] - self.score_history[-2]
                if delta > 0:
                    task += f"\nTrend: Improving (+{delta:.1f}), continue refining."
                elif delta < 0:
                    task += f"\nTrend: Declining ({delta:.1f}), consider pivoting."
                else:
                    task += f"\nTrend: Flat, try a different approach."

        task += "\n\nCommit with git when done."
        return task

    def _parse_score(self, text: str) -> float:
        """解析分数"""
        patterns = [
            r'SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10',
            r'Score:\s*(\d+(?:\.\d+)?)\s*/\s*10',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return float(match.group(1))
        log.warning("Could not parse score, defaulting to 0")
        return 0.0


def main():
    parser = argparse.ArgumentParser(description="Harness - Multi-agent development")
    parser.add_argument("prompt", nargs="?", default="Build a Pomodoro timer with start, pause, reset buttons")
    parser.add_argument("--workspace", default=config.WORKSPACE)
    args = parser.parse_args()

    harness = Harness(args.workspace)
    result = harness.run(args.prompt)

    log.info("\n" + "="*60)
    log.info("Final Result")
    log.info("="*60)
    log.info(f"Success: {result['success']}")
    log.info(f"Score: {result.get('score', 0)}")
    log.info(f"Rounds: {result['rounds']}")

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
