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
            self.commit_history = [tuple(e) for e in state.get("commit_history", [])]
            self.strategy_history = state.get("strategy_history", [])
            self.token_totals = state.get("token_totals", {"prompt": 0, "completion": 0})
            self.round_stats = state.get("round_stats", [])
            self._resumed = True
            log.info(
                f"[state] Resumed from {config.STATE_FILE} — "
                f"{self._completed_rounds} round(s) already completed, "
                f"last score: {self.score_history[-1] if self.score_history else 'n/a'}"
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
            log.info(
                f"Resuming from round {self._completed_rounds + 1} "
                f"(completed: {self._completed_rounds}, "
                f"last score: {self.score_history[-1] if self.score_history else 'n/a'})"
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
            self.score_history.append(score)

            # Persist state immediately after every round so an interruption at any
            # later point (the next round's planning, contract, or build) is recoverable.
            self._completed_rounds = round_num
            self._save_state()

            if score >= config.PASS_THRESHOLD:
                log.info(f"\n🎉 Success! Final score: {score}")
                self._log_final_stats()
                self._clear_state()
                return {
                    "success": True, "score": score, "rounds": round_num,
                    "token_totals": dict(self.token_totals),
                    "round_stats": list(self.round_stats),
                }

            if round_num < config.MAX_ROUNDS:
                log.info(f"Score {score} below threshold {config.PASS_THRESHOLD}, continuing...")

        self._log_final_stats()
        # Leave STATE_FILE in place on failure so the run can be resumed or inspected.
        return {
            "success": False,
            "score": self.score_history[-1] if self.score_history else 0,
            "rounds": len(self.score_history),
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

        score = self._parse_score(eval_text)

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
        round_prompt = build_usage["prompt"] + eval_usage["prompt"]
        round_completion = build_usage["completion"] + eval_usage["completion"]
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

            if len(self.score_history) >= 2:
                delta = self.score_history[-1] - self.score_history[-2]
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

        log.info(
            f"[stats] Round {round_num:>2} | score {score:>4.1f}/10 | "
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
            f"{'Round':>5} | {'Score':>5} | {'Strategy':>8} | "
            f"{'Prompt':>7} | {'Compl.':>7} | {'Total':>7} | {'Time(s)':>7}"
        )
        log.info("-"*72)
        for s in self.round_stats:
            total = s["prompt_tokens"] + s["completion_tokens"]
            log.info(
                f"{s['round']:>5} | {s['score']:>5.1f} | {s['strategy']:>8} | "
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
