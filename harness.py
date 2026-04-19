#!/usr/bin/env python3
"""
Harness — 多 Agent 长时间自主开发架构（生产级）

基于 Anthropic 文章 "Harness design for long-running application development"
"""
from __future__ import annotations

import argparse
import json
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
    CODE_REVIEWER_SYSTEM, BROWSER_TESTER_SYSTEM,
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
        self._setup_file_logging()

        # 创建 Agent
        self.planner = Agent("Planner", PLANNER_SYSTEM, TOOL_SCHEMAS)
        self.sprint_planner = Agent("SprintPlanner", SPRINT_PLANNER_SYSTEM, TOOL_SCHEMAS)
        self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS)
        self.evaluator = Agent("Evaluator", EVALUATOR_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS)

        self.score_history: list[float] = []          # 保留兼容旧 state
        self.sprint_score_history: list[float] = []     # 本轮 Sprint 质量（基于 sprint_contract）
        self.overall_score_history: list[float] = []    # 全局质量（基于 contract.md，加权计算）
        # 与 score_history 下标严格对齐：commit_history[i] 对应第 i 轮的 (round_num, hash)
        self.commit_history: list[tuple[int, str]] = []

        # Strategy decisions declared by the Builder at the end of each round.
        # Each entry is a dict: {"strategy": "REFINE"|"PIVOT", "reason": str, "new_direction": str|None}
        self.strategy_history: list[dict] = []

        # Cost / time tracking — cumulative across all agents and rounds.
        # token_totals keys: "prompt", "completion"
        self.token_totals: dict[str, int] = {"prompt": 0, "completion": 0}
        # Per-round breakdown: list of {"round": int, "prompt": int, "completion": int, "elapsed_s": float}
        self.round_stats: list[dict] = []

        # Tracks how many rounds were already completed when this instance was created.
        # Used by run() to skip phases and rounds that were done in a previous session.
        self._completed_rounds: int = 0
        self._resumed: bool = False

        self._load_state()

    # ------------------------------------------------------------------ #
    #  Persistence — save / load harness state for interrupt recovery     #
    # ------------------------------------------------------------------ #

    def _state_path(self) -> Path:
        return self.workspace / config.STATE_FILE

    def _save_state(self) -> None:
        """Persist mutable harness state to STATE_FILE after each round.

        The file is written atomically (write to a temp file, then rename)
        to avoid corruption from mid-write interruptions.
        """
        state = {
            "completed_rounds": self._completed_rounds,
            "score_history": self.score_history,
            "sprint_score_history": self.sprint_score_history,
            "overall_score_history": self.overall_score_history,
            # commit_history entries are (round_num, hash) tuples — serialise as lists
            "commit_history": [list(e) for e in self.commit_history],
            "strategy_history": self.strategy_history,
            "token_totals": self.token_totals,
            "round_stats": self.round_stats,
        }
        tmp = self._state_path().with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(self._state_path())
            log.debug(f"[state] Saved to {config.STATE_FILE}")
        except Exception as e:
            log.warning(f"[state] Failed to save state: {e}")

    def _load_state(self) -> None:
        """Load persisted state if STATE_FILE exists.

        On success, restores all history lists and sets self._resumed = True
        so run() can skip already-completed phases and rounds.
        """
        path = self._state_path()
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._completed_rounds = int(state.get("completed_rounds", 0))
            self.score_history = [float(s) for s in state.get("score_history", [])]
            self.sprint_score_history = [float(s) for s in state.get("sprint_score_history", [])]
            self.overall_score_history = [float(s) for s in state.get("overall_score_history", [])]
            # 兼容旧 state：如果新结构不存在，从 score_history 复制
            if not self.sprint_score_history and self.score_history:
                self.sprint_score_history = list(self.score_history)
            if not self.overall_score_history and self.score_history:
                self.overall_score_history = list(self.score_history)
            self.commit_history = [tuple(e) for e in state.get("commit_history", [])]
            self.strategy_history = state.get("strategy_history", [])
            self.token_totals = state.get("token_totals", {"prompt": 0, "completion": 0})
            self.round_stats = state.get("round_stats", [])
            self._resumed = True
            last_overall = self.overall_score_history[-1] if self.overall_score_history else 'n/a'
            log.info(
                f"[state] Resumed from {config.STATE_FILE} — "
                f"{self._completed_rounds} round(s) already completed, "
                f"last overall: {last_overall}"
            )
        except Exception as e:
            log.warning(f"[state] Could not load state (will start fresh): {e}")

    def _clear_state(self) -> None:
        """Remove STATE_FILE after a successful run so the workspace is clean."""
        path = self._state_path()
        try:
            if path.exists():
                path.unlink()
                log.debug(f"[state] Cleared {config.STATE_FILE}")
        except Exception as e:
            log.warning(f"[state] Could not remove state file: {e}")

    def _setup_file_logging(self):
        """为当前 workspace 设置独立的文件日志 Handler，支持实时监控。"""
        log_path = self.workspace / "harness.log"
        # 避免重复添加（恢复运行时可能重新实例化 Harness）
        for h in log.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == str(log_path):
                return
        file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ))
        log.addHandler(file_handler)
        log.info(f"[logging] File logging enabled: {log_path}")

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
        """Run the full Planner → Contract → Build-Evaluate loop.

        If a previous run was interrupted and STATE_FILE exists in the workspace,
        the harness automatically resumes from where it left off:
        - Phase 1 (Plan) is skipped when spec.md already exists.
        - Phase 2 (Contract) is skipped when contract.md already exists.
        - Already-completed rounds are skipped; the loop starts from the next round.
        """
        log.info("="*60)
        log.info("Harness %s", "resuming" if self._resumed else "starting")
        log.info("="*60)
        log.info(f"Workspace: {self.workspace}")
        log.info(f"Prompt: {user_prompt}")
        if self._resumed:
            last_sprint = self.sprint_score_history[-1] if self.sprint_score_history else 'n/a'
            last_overall = self.overall_score_history[-1] if self.overall_score_history else 'n/a'
            log.info(
                f"Resuming from round {self._completed_rounds + 1} "
                f"(completed: {self._completed_rounds}, "
                f"last sprint: {last_sprint}, last overall: {last_overall})"
            )

        spec_path = self.workspace / config.SPEC_FILE
        contract_path = self.workspace / config.CONTRACT_FILE

        # Phase 1: Plan — skip if spec.md already exists (resumed run)
        if spec_path.exists():
            log.info("Phase 1: Plan — SKIPPED (spec.md already exists)")
        else:
            log.info("\n" + "="*60)
            log.info("Phase 1: Plan")
            log.info("="*60)

            self.planner.run(
                f"Create a product specification for:\n\n{user_prompt}\n\n"
                f"Save to {config.SPEC_FILE}"
            )

            if not spec_path.exists():
                log.error("Planner failed to create spec.md — check if the model triggered write_file")
                return {"success": False, "error": "Planner failed to create spec", "rounds": 0, "score": 0}

            log.info("Spec created successfully")

        # Phase 2: Contract — skip if contract.md already exists (resumed run)
        if contract_path.exists():
            log.info("Phase 2: Contract — SKIPPED (contract.md already exists)")
        else:
            log.info("\n" + "="*60)
            log.info("Phase 2: Contract")
            log.info("="*60)
            self._negotiate_contract()

        # Phase 3+: Build-Evaluate loop — start from the next unfinished round
        start_round = self._completed_rounds + 1
        for round_num in range(start_round, config.MAX_ROUNDS + 1):
            log.info("\n" + "="*60)
            log.info(f"Round {round_num}/{config.MAX_ROUNDS}")
            log.info("="*60)

            score = self._build_round(round_num)
            # 注意：_build_round 内部已经 append 了 sprint/overall/score_history

            # Persist state immediately after every round so an interruption at any
            # later point (the next round's planning, contract, or build) is recoverable.
            self._completed_rounds = round_num
            self._save_state()

            if score >= config.PASS_THRESHOLD:
                log.info(f"\n🎉 Success! Final overall score: {score}")
                self._log_final_stats()
                self._clear_state()
                return {
                    "success": True, "score": score, "rounds": round_num,
                    "token_totals": dict(self.token_totals),
                    "round_stats": list(self.round_stats),
                }

            if round_num < config.MAX_ROUNDS:
                log.info(f"Overall score {score:.1f} below threshold {config.PASS_THRESHOLD}, continuing...")

        self._log_final_stats()
        # Leave STATE_FILE in place on failure so the run can be resumed or inspected.
        final_score = self.overall_score_history[-1] if self.overall_score_history else 0
        return {
            "success": False,
            "score": final_score,
            "rounds": len(self.overall_score_history),
            "token_totals": dict(self.token_totals),
            "round_stats": list(self.round_stats),
        }

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
        round_start = time.time()

        # 阶段化回退策略（双轨评分）
        # 1. Sprint 不及格 → 回退到本轮开始前的状态（修复当前阶段）
        # 2. Overall 显著退化 → 回退到历史最优 overall（全局方向错误）
        # 3. 其他情况 → 继续下一轮 Sprint
        rollback_msg = ""

        # 检查 1: 上一轮 Sprint 不及格
        if self.sprint_score_history and self.sprint_score_history[-1] < config.SPRINT_PASS_THRESHOLD:
            if self.commit_history:
                # 回退到最新 commit（上一轮结束时的状态），强制重做/修复
                _, latest_hash = self.commit_history[-1]
                self._rollback_to(
                    latest_hash,
                    f"Sprint score {self.sprint_score_history[-1]:.1f} below threshold {config.SPRINT_PASS_THRESHOLD}"
                )
                rollback_msg = (
                    f"\nNOTE: Last sprint scored {self.sprint_score_history[-1]:.1f} "
                    f"(below threshold {config.SPRINT_PASS_THRESHOLD}). "
                    f"You MUST fix the failing criteria from the last sprint BEFORE adding new features. "
                    f"Do NOT move on to new tasks until this sprint passes."
                )
            else:
                rollback_msg = (
                    f"\nNOTE: Last sprint scored {self.sprint_score_history[-1]:.1f} "
                    f"(below threshold {config.SPRINT_PASS_THRESHOLD}). "
                    f"Fix the current implementation before proceeding."
                )

        # 检查 2: Overall 显著退化（Sprint 及格但整体趋势向下）
        elif self.overall_score_history and len(self.overall_score_history) >= 2:
            best_idx = max(range(len(self.overall_score_history)), key=lambda i: self.overall_score_history[i])
            best_overall = self.overall_score_history[best_idx]
            last_overall = self.overall_score_history[-1]
            if last_overall < best_overall - config.SIGNIFICANT_DROP:
                if len(self.commit_history) > best_idx:
                    _, best_hash = self.commit_history[best_idx]
                    self._rollback_to(
                        best_hash,
                        f"Overall dropped {last_overall:.1f} → best was {best_overall:.1f} at round {best_idx + 1}"
                    )
                    rollback_msg = (
                        f"\nNOTE: Overall score dropped to {last_overall:.1f}. "
                        f"Rolled back to round {best_idx + 1} (best overall {best_overall:.1f}). "
                        f"The last approach broke existing functionality. Fix or change strategy."
                    )

        # Sprint 规划：决定本轮 Builder 聚焦的 1-2 个任务
        self._plan_sprint(round_num)

        # Per-sprint contract: negotiate a focused Definition of Done for this round only
        self._negotiate_sprint_contract(round_num)

        # Build
        log.info("Build phase")
        build_task = self._build_build_task(round_num, rollback_msg)
        build_result, build_usage = self.builder.run_with_stats(build_task)

        # Parse the Builder's strategy declaration from this round's output
        strategy = self._parse_strategy(build_result)
        self.strategy_history.append(strategy)
        log.info(
            f"Builder strategy: {strategy['strategy']}"
            + (f" — {strategy['reason']}" if strategy['reason'] else "")
        )
        if strategy['strategy'] == 'PIVOT' and strategy.get('new_direction'):
            log.info(f"  New direction: {strategy['new_direction']}")

        # Harness 层兜底快照，确保即使 Builder 跳过提交也有版本记录
        head_hash = self._commit_round(round_num)
        if head_hash:
            self.commit_history.append((round_num, head_hash))

        # Evaluate — 三层分工：CodeReviewer → BrowserTester → Evaluator(打分)
        # 核心价值：子 Agent 的上下文相互隔离，不会污染父 Agent 的 messages 列表
        sprint_contract_path = self.workspace / config.SPRINT_CONTRACT_FILE
        contract_ref = config.SPRINT_CONTRACT_FILE if sprint_contract_path.exists() else config.CONTRACT_FILE

        # Step 1: Code Review
        log.info("Evaluate phase — Step 1: Code Review")
        code_reviewer = Agent("CodeReviewer", CODE_REVIEWER_SYSTEM, TOOL_SCHEMAS)
        code_review_result, code_review_usage = code_reviewer.run_with_stats(
            f"Review the codebase against {contract_ref} and {config.CONTRACT_FILE}. "
            f"List files examined, critical issues, warnings, and feature coverage estimate."
        )

        # Step 2: Browser Test
        log.info("Evaluate phase — Step 2: Browser Test")
        browser_tester = Agent("BrowserTester", BROWSER_TESTER_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS)
        browser_result, browser_usage = browser_tester.run_with_stats(
            f"Test the web app. Verify criteria from {contract_ref}. "
            f"Run both desktop (1280x720) and mobile (375x812) tests. Report PASS/FAIL per criterion."
        )

        # Step 3: Scoring — Evaluator 基于前两者的报告打分，不再自己做代码审查和浏览器测试
        log.info("Evaluate phase — Step 3: Scoring")
        # 截断报告，防止 Evaluator 上下文爆炸
        code_review_summary = code_review_result[:5000] if len(code_review_result) > 5000 else code_review_result
        browser_summary = browser_result[:5000] if len(browser_result) > 5000 else browser_result

        eval_task = f"""You are the lead QA engineer. Synthesize the following specialist reports into a final evaluation.

You do NOT need to read source files or run browser tests — the specialists have already done that.
Your job is to apply the scoring rubric and write the final feedback.

## Code Review Report
{code_review_summary}

## Browser Test Report
{browser_summary}

## Instructions
1. Read {contract_ref} for the acceptance criteria context.
2. Read {config.CONTRACT_FILE} for broader quality standards.
3. Score each dimension with concrete evidence from the reports above.
4. Calculate and output BOTH scores:
   - SPRINT_SCORE: X/10 (how well this sprint's tasks were completed)
   - OVERALL_SCORE: X/10 (weighted overall, using the 40/30/15/15 formula)
5. Save feedback to {config.FEEDBACK_FILE}.
"""
        eval_result, eval_usage = self.evaluator.run_with_stats(eval_task)

        # Prefer reading feedback.md directly: the Evaluator writes its full structured report
        # there via write_file, and the final assistant message is typically just a short
        # confirmation ("Saved feedback to feedback.md") that contains no scores.
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = eval_result
        else:
            eval_text = eval_result

        sprint_score, overall_score = self._parse_scores(eval_text)
        self.sprint_score_history.append(sprint_score)
        self.overall_score_history.append(overall_score)
        # 保留兼容：score_history 也记录 overall_score
        self.score_history.append(overall_score)
        log.info(f"  Sprint score: {sprint_score:.1f}/10 | Overall score: {overall_score:.1f}/10")

        # Parse per-dimension scores and log them
        dim_scores = self._parse_dimension_scores(eval_text)
        if dim_scores:
            for dim, s in sorted(dim_scores.items()):
                threshold = config.DIMENSION_THRESHOLDS.get(dim, 0)
                status = "OK" if s >= threshold else "FAIL"
                log.info(f"  [{status}] {dim}: {s}/10 (threshold {threshold})")
        else:
            log.warning("Could not parse per-dimension scores from evaluator feedback")

        # Hard threshold check: if any dimension is below its threshold, force the round
        # to fail even if the overall score is above PASS_THRESHOLD.
        score = overall_score  # 用于后续判断的分数
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

        # Accumulate token usage and record per-round stats
        # 包含 Build + CodeReview + BrowserTest + Scoring 四个阶段的 token
        round_prompt = (
            build_usage["prompt"]
            + code_review_usage["prompt"]
            + browser_usage["prompt"]
            + eval_usage["prompt"]
        )
        round_completion = (
            build_usage["completion"]
            + code_review_usage["completion"]
            + browser_usage["completion"]
            + eval_usage["completion"]
        )
        self.token_totals["prompt"] += round_prompt
        self.token_totals["completion"] += round_completion
        elapsed = time.time() - round_start
        self.round_stats.append({
            "round": round_num,
            "score": score,
            "strategy": strategy["strategy"],
            "prompt_tokens": round_prompt,
            "completion_tokens": round_completion,
            "elapsed_s": elapsed,
        })
        self._log_round_stats(round_num, score, round_prompt, round_completion, elapsed)

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

            if len(self.overall_score_history) >= 2:
                delta = self.overall_score_history[-1] - self.overall_score_history[-2]
                if delta > 0:
                    task += f"\nTrend: Improving (+{delta:.1f}), continue refining."
                elif delta < 0:
                    task += f"\nTrend: Declining ({delta:.1f}), consider pivoting."
                else:
                    task += f"\nTrend: Flat, try a different approach."

        # Inject the previous round's strategy decision
        if self.strategy_history:
            prev = self.strategy_history[-1]
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

    def _parse_strategy(self, text: str) -> dict:
        """Extract the STRATEGY / REASON / NEW DIRECTION block from Builder output.

        Returns a dict with keys: strategy ("REFINE"|"PIVOT"|"UNKNOWN"), reason (str), new_direction (str|None).
        """
        result = {"strategy": "UNKNOWN", "reason": "", "new_direction": None}

        strategy_match = re.search(r'STRATEGY:\s*(REFINE|PIVOT)', text, re.IGNORECASE)
        if strategy_match:
            result["strategy"] = strategy_match.group(1).upper()

        reason_match = re.search(r'REASON:\s*(.+)', text)
        if reason_match:
            result["reason"] = reason_match.group(1).strip()

        direction_match = re.search(r'NEW DIRECTION:\s*(.+?)(?:\n---|\Z)', text, re.DOTALL)
        if direction_match:
            result["new_direction"] = direction_match.group(1).strip()

        if result["strategy"] == "UNKNOWN":
            log.warning("Builder did not include a STRATEGY declaration — defaulting to REFINE")
            result["strategy"] = "REFINE"

        return result

    def _log_round_stats(
        self, round_num: int, score: float,
        prompt_tokens: int, completion_tokens: int, elapsed_s: float
    ) -> None:
        """Print a per-round cost / time summary row and the running cumulative total."""
        total_tokens = prompt_tokens + completion_tokens
        cum_prompt = self.token_totals["prompt"]
        cum_completion = self.token_totals["completion"]
        cum_total = cum_prompt + cum_completion

        # 显示双轨分数
        sprint_s = self.sprint_score_history[-1] if self.sprint_score_history else 0.0
        overall_s = self.overall_score_history[-1] if self.overall_score_history else 0.0

        log.info(
            f"[stats] Round {round_num:>2} | sprint {sprint_s:>4.1f} | overall {overall_s:>4.1f} | "
            f"tokens: {prompt_tokens:>6}p + {completion_tokens:>6}c = {total_tokens:>7} | "
            f"elapsed: {elapsed_s:>6.1f}s"
        )
        log.info(
            f"[stats] Cumulative          | "
            f"tokens: {cum_prompt:>6}p + {cum_completion:>6}c = {cum_total:>7} | "
            f"rounds: {len(self.round_stats)}"
        )

    def _log_final_stats(self) -> None:
        """Print the final cost / time summary table at the end of the run."""
        if not self.round_stats:
            return

        log.info("\n" + "="*72)
        log.info("Cost / Time Summary")
        log.info("="*72)
        log.info(
            f"{'Round':>5} | {'Sprint':>6} | {'Overall':>7} | {'Strategy':>8} | "
            f"{'Prompt':>7} | {'Compl.':>7} | {'Total':>7} | {'Time(s)':>7}"
        )
        log.info("-"*80)
        for i, s in enumerate(self.round_stats):
            total = s["prompt_tokens"] + s["completion_tokens"]
            sprint_s = self.sprint_score_history[i] if i < len(self.sprint_score_history) else 0.0
            overall_s = self.overall_score_history[i] if i < len(self.overall_score_history) else 0.0
            log.info(
                f"{s['round']:>5} | {sprint_s:>6.1f} | {overall_s:>7.1f} | {s['strategy']:>8} | "
                f"{s['prompt_tokens']:>7} | {s['completion_tokens']:>7} | "
                f"{total:>7} | {s['elapsed_s']:>7.1f}"
            )
        log.info("-"*72)
        grand_total = self.token_totals["prompt"] + self.token_totals["completion"]
        total_time = sum(s["elapsed_s"] for s in self.round_stats)
        log.info(
            f"{'TOTAL':>5} | {'':>5} | {'':>8} | "
            f"{self.token_totals['prompt']:>7} | {self.token_totals['completion']:>7} | "
            f"{grand_total:>7} | {total_time:>7.1f}"
        )
        log.info("="*72)

    def _parse_scores(self, text: str) -> tuple[float, float]:
        """Parse SPRINT_SCORE and OVERALL_SCORE from evaluator feedback.

        Returns (sprint_score, overall_score).
        If new format not found, falls back to legacy SCORE line.
        """
        sprint_match = re.search(r'SPRINT_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
        overall_match = re.search(r'OVERALL_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)

        if sprint_match and overall_match:
            return float(sprint_match.group(1)), float(overall_match.group(1))

        # Fallback: legacy single SCORE line — treat both as the same
        legacy_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
        if legacy_match:
            score = float(legacy_match.group(1))
            log.warning("Legacy single SCORE found — using it for both sprint and overall")
            return score, score

        log.warning("Could not parse any score, defaulting to 0")
        return 0.0, 0.0

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
    args = parser.parse_args()

    # 未手动指定 --workspace 时，自动生成带时间戳的独立目录
    workspace = args.workspace if args.workspace else _make_workspace(args.prompt)
    log.info(f"Workspace: {workspace}")

    # --reset: wipe the state file so the run starts from scratch
    if args.reset:
        state_path = Path(workspace) / config.STATE_FILE
        if state_path.exists():
            state_path.unlink()
            log.info(f"[state] --reset: deleted {config.STATE_FILE}")

    harness = Harness(workspace)
    result = harness.run(args.prompt)

    log.info("\n" + "="*60)
    log.info("Final Result")
    log.info("="*60)
    log.info(f"Success: {result['success']}")
    log.info(f"Score: {result.get('score', 0)}")
    log.info(f"Rounds: {result.get('rounds', 0)}")
    if "token_totals" in result:
        t = result["token_totals"]
        log.info(f"Total tokens: {t.get('prompt', 0)} prompt + {t.get('completion', 0)} completion")
    if "error" in result:
        log.error(f"Error: {result['error']}")

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
