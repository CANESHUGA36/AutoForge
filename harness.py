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
import time
from pathlib import Path

import config
from agents import Agent
from prompts import (
    PLANNER_SYSTEM, BUILDER_SYSTEM, EVALUATOR_SYSTEM,
    CONTRACT_BUILDER_SYSTEM, CONTRACT_REVIEWER_SYSTEM,
    SPRINT_PLANNER_SYSTEM,
    SPRINT_CONTRACT_BUILDER_SYSTEM, SPRINT_CONTRACT_REVIEWER_SYSTEM,
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
        # 同步到 config.WORKSPACE，确保 tools._resolve() 使用正确的工作目录
        config.WORKSPACE = str(self.workspace.resolve())
        self._init_git()

        # 创建 Agent
        self.planner = Agent("Planner", PLANNER_SYSTEM, TOOL_SCHEMAS)
        self.sprint_planner = Agent("SprintPlanner", SPRINT_PLANNER_SYSTEM, TOOL_SCHEMAS)
        self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS)
        self.evaluator = Agent("Evaluator", EVALUATOR_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS)

        self.score_history: list[float] = []
        # 与 score_history 下标严格对齐：commit_history[i] 对应第 i 轮的 (round_num, hash)
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
            log.error("Planner failed to create spec.md — check if the model triggered write_file")
            return {"success": False, "error": "Planner failed to create spec", "rounds": 0, "score": 0}

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

    def _plan_sprint(self, round_num: int) -> None:
        """在 Builder 启动前，由 Sprint Planner 生成本轮聚焦的任务清单（sprint.md）。"""
        log.info("Sprint planning phase")
        feedback_hint = ""
        sprint_path = self.workspace / config.SPRINT_FILE
        feedback_path = self.workspace / config.FEEDBACK_FILE

        if sprint_path.exists():
            feedback_hint += f"Read {config.SPRINT_FILE} to understand what was attempted last round.\n"
        if feedback_path.exists():
            feedback_hint += f"Read {config.FEEDBACK_FILE} to understand what issues were found.\n"

        task = (
            f"Plan sprint {round_num}.\n\n"
            f"Steps:\n"
            f"1. Read {config.SPEC_FILE} for the full feature list.\n"
            f"2. Read {config.CONTRACT_FILE} for acceptance criteria.\n"
            f"3. Use list_files to see what source files already exist.\n"
        )
        if feedback_hint:
            task += feedback_hint
        task += (
            f"\nSelect 1-2 tasks for this round and save to {config.SPRINT_FILE}.\n"
            f"Be specific and realistic — the Builder must finish them in one session."
        )
        self.sprint_planner.run(task)

        if not sprint_path.exists():
            log.warning("SprintPlanner did not create sprint.md, Builder will fall back to spec.md")

    def _negotiate_sprint_contract(self, round_num: int) -> None:
        """Negotiate a per-sprint acceptance contract between a dedicated contract writer and reviewer.

        The contract is saved to SPRINT_CONTRACT_FILE and covers only the tasks in sprint.md for
        this round, giving the Evaluator a focused, round-specific Definition of Done.
        """
        log.info("Sprint contract negotiation phase")

        sprint_path = self.workspace / config.SPRINT_FILE
        if not sprint_path.exists():
            log.warning("sprint.md not found — skipping per-sprint contract negotiation")
            return

        writer = Agent("SprintContractWriter", SPRINT_CONTRACT_BUILDER_SYSTEM, TOOL_SCHEMAS)
        reviewer = Agent("SprintContractReviewer", SPRINT_CONTRACT_REVIEWER_SYSTEM, TOOL_SCHEMAS)

        for attempt in range(3):
            log.info(f"Sprint contract negotiation attempt {attempt + 1}/3")

            writer.run(
                f"Round {round_num}: Read {config.SPRINT_FILE} and {config.CONTRACT_FILE}, "
                f"then write the sprint contract to {config.SPRINT_CONTRACT_FILE}."
            )

            sprint_contract_path = self.workspace / config.SPRINT_CONTRACT_FILE
            if not sprint_contract_path.exists():
                log.warning("SprintContractWriter did not create sprint_contract.md")
                continue

            review = reviewer.run(
                f"Review {config.SPRINT_CONTRACT_FILE} against {config.SPRINT_FILE}. "
                f"Reply APPROVED or list issues."
            )

            if "APPROVED" in review:
                log.info(f"Sprint contract approved on attempt {attempt + 1}")
                return

            log.info(f"Sprint contract review feedback: {review[:200]}...")

        log.info("Sprint contract negotiation max attempts reached — using last written version")

    def _get_head_hash(self) -> str | None:
        """获取当前 HEAD 的 commit hash，若仓库尚无提交则返回 None。"""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _commit_round(self, round_num: int) -> str | None:
        """在每轮 Build 结束后强制做一次 git 快照，确保即使 Builder 忘记提交也有历史记录。

        返回操作后的 HEAD commit hash；若仓库尚无提交则返回 None。"""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        if not result.stdout.strip():
            log.info(f"[git] Nothing to commit after round {round_num}")
        else:
            subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
            msg = f"harness: round {round_num} snapshot"
            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.workspace, capture_output=True, text=True,
                **config.SUBPROCESS_TEXT_KWARGS,
            )
            if commit.returncode == 0:
                log.info(f"[git] Committed round {round_num} snapshot")
            else:
                log.warning(f"[git] Commit failed: {commit.stderr.strip()}")
        return self._get_head_hash()

    def _rollback_to(self, commit_hash: str, reason: str) -> None:
        """将 workspace 硬重置到指定 commit，并记录回滚原因到日志。"""
        log.info(f"[git] Rolling back to {commit_hash[:8]} — {reason}")
        result = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        if result.returncode == 0:
            log.info("[git] Rollback successful")
        else:
            log.warning(f"[git] Rollback failed: {result.stderr.strip()}")

    def _build_round(self, round_num: int) -> float:
        """执行一轮 Build-Evaluate"""
        # 回滚检查：若上一轮分数低于历史最高分，在 Builder 启动前先恢复到最优版本。
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

        # Sprint 规划：决定本轮 Builder 聚焦的 1-2 个任务
        self._plan_sprint(round_num)

        # Per-sprint contract: negotiate a focused Definition of Done for this round only
        self._negotiate_sprint_contract(round_num)

        # Build
        log.info("Build phase")
        build_task = self._build_build_task(round_num, rollback_msg)
        self.builder.run(build_task)

        # Harness 层兜底快照，确保即使 Builder 跳过提交也有版本记录
        head_hash = self._commit_round(round_num)
        if head_hash:
            self.commit_history.append((round_num, head_hash))

        # Evaluate — use per-sprint contract when available, otherwise fall back to global contract
        log.info("Evaluate phase")
        sprint_contract_path = self.workspace / config.SPRINT_CONTRACT_FILE
        if sprint_contract_path.exists():
            criteria_instruction = (
                f"1. Read {config.SPRINT_CONTRACT_FILE} — this is the PRIMARY criteria for this round.\n"
                f"   Also read {config.CONTRACT_FILE} for broader quality standards.\n"
            )
        else:
            criteria_instruction = f"1. Read {config.CONTRACT_FILE} for criteria.\n"

        eval_task = f"""Evaluate the current code against acceptance criteria.

{criteria_instruction}2. Examine code files in the workspace
3. If it's a web app, use run_bash to start the dev server (e.g. `npm run dev &`),
   then call browser_test with url="http://localhost:5173" and relevant actions
   to verify each criterion from the sprint contract
4. Give a score and detailed feedback
5. Save feedback to {config.FEEDBACK_FILE}

Include "SCORE: X/10" in your feedback.
"""
        eval_result = self.evaluator.run(eval_task)
        score = self._parse_score(eval_result)

        # Parse per-dimension scores and log them
        dim_scores = self._parse_dimension_scores(eval_result)
        if dim_scores:
            for dim, s in sorted(dim_scores.items()):
                threshold = config.DIMENSION_THRESHOLDS.get(dim, 0)
                status = "OK" if s >= threshold else "FAIL"
                log.info(f"  [{status}] {dim}: {s}/10 (threshold {threshold})")
        else:
            log.warning("Could not parse per-dimension scores from evaluator feedback")

        # Hard threshold check: if any dimension is below its threshold, force the round
        # to fail even if the overall score is above PASS_THRESHOLD.
        failed_dims = self._check_dimension_thresholds(dim_scores)
        if failed_dims:
            log.warning(f"Hard threshold(s) failed: {', '.join(failed_dims)}")
            # Cap the effective score just below the pass threshold so the loop continues
            if score >= config.PASS_THRESHOLD:
                log.warning(
                    f"Overall score {score} would have passed, but dimension hard threshold "
                    f"forces continuation. Effective score capped to {config.PASS_THRESHOLD - 0.1}."
                )
                score = config.PASS_THRESHOLD - 0.1

        log.info(f"Round score: {score}/10")
        return score

    def _build_build_task(self, round_num: int, rollback_msg: str = "") -> str:
        """Build the task prompt for the Builder agent."""
        sprint_path = self.workspace / config.SPRINT_FILE
        sprint_contract_path = self.workspace / config.SPRINT_CONTRACT_FILE
        feedback_path = self.workspace / config.FEEDBACK_FILE

        # Primary guide: prefer sprint.md; fall back to spec.md
        if sprint_path.exists():
            primary_guide = (
                f"1. Read {config.SPRINT_FILE} — this is your ONLY task list for this round.\n"
            )
        else:
            primary_guide = (
                f"1. Read {config.SPEC_FILE} for product spec.\n"
            )

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
        """Parse the overall SCORE: X/10 from evaluator feedback."""
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

    def _parse_dimension_scores(self, text: str) -> dict:
        """Parse per-dimension scores from '### Dimension Name: X/10' headings."""
        # Canonical name mapping: raw heading text -> config key
        _name_map = {
            "design quality": "design_quality",
            "design_quality": "design_quality",
            "originality":    "originality",
            "craft":          "craft",
            "functionality":  "functionality",
        }
        scores: dict = {}
        pattern = r'###\s*([\w\s]+?):\s*(\d+(?:\.\d+)?)\s*/\s*10'
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw = match.group(1).strip().lower()
            key = _name_map.get(raw)
            if key:
                scores[key] = float(match.group(2))
        return scores

    def _check_dimension_thresholds(self, dim_scores: dict) -> list:
        """Return list of human-readable failure strings for dimensions below hard thresholds.

        Also checks for explicit DIMENSION_FAIL markers written by the Evaluator.
        """
        failed = []
        for dim, threshold in config.DIMENSION_THRESHOLDS.items():
            score = dim_scores.get(dim)
            if score is not None and score < threshold:
                failed.append(f"{dim}={score:.1f} (threshold {threshold:.1f})")
        return failed


def _make_workspace(prompt: str) -> str:
    """根据 prompt 和时间戳自动生成独立的工作目录路径。"""
    slug = re.sub(r'[^\w\s-]', '', prompt.lower())
    slug = re.sub(r'\s+', '-', slug.strip())[:30].rstrip('-')
    ts = time.strftime("%Y%m%d-%H%M%S")
    # Use PROJECTS_DIR so Docker (HARNESS_PROJECTS_DIR=/projects) writes into the bind mount.
    return str(Path(config.PROJECTS_DIR) / f"{slug}-{ts}")


def main():
    parser = argparse.ArgumentParser(description="Harness - Multi-agent development")
    parser.add_argument("prompt", nargs="?", default="Build a Pomodoro timer with start, pause, reset buttons")
    parser.add_argument("--workspace", default=None,
                        help="工作目录路径。不指定时在 PROJECTS_DIR（默认 ./projects，Docker 为 /projects）下生成 <slug>-<timestamp>/")
    args = parser.parse_args()

    # 未手动指定 --workspace 时，自动生成带时间戳的独立目录
    workspace = args.workspace if args.workspace else _make_workspace(args.prompt)
    log.info(f"Workspace: {workspace}")

    harness = Harness(workspace)
    result = harness.run(args.prompt)

    log.info("\n" + "="*60)
    log.info("Final Result")
    log.info("="*60)
    log.info(f"Success: {result['success']}")
    log.info(f"Score: {result.get('score', 0)}")
    log.info(f"Rounds: {result.get('rounds', 0)}")
    if "error" in result:
        log.error(f"Error: {result['error']}")

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
