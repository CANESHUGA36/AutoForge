#!/usr/bin/env python3
"""
Harness        ?
    Anthropic     "Harness design for long-running application development"
"""
from __future__ import annotations
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import config
from agents import Agent
from dashboard import Dashboard
from eval_cache import EvalCache
from prompts import (
    PLANNER_SYSTEM, BUILDER_SYSTEM, EVALUATOR_SYSTEM,
    CONTRACT_BUILDER_SYSTEM,
    SPRINT_PLANNER_SYSTEM,
    SPRINT_CONTRACT_BUILDER_SYSTEM,
    CODE_REVIEWER_SYSTEM, BROWSER_TESTER_SYSTEM,
)
from tools_impl import TOOL_SCHEMAS, BROWSER_TOOL_SCHEMAS
from harness.build import build_build_task, verify_dev_server
from harness.eval import parse_scores, parse_dimension_scores, check_dimension_thresholds
from harness.git import GitManager
from harness.logging import setup_file_logging, log_round_stats, log_final_stats
from harness.sprint import plan_sprint, negotiate_contract
from harness.state import StateManager
from harness.strategy import parse_strategy
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("harness")
class Harness:
    """Harness     ? ?    ?Agent               """
    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        config.WORKSPACE = str(self.workspace.resolve())
        #                 Logger
        self.log = logging.getLogger(f"harness.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        #          
        self.git = GitManager(self.workspace)
        self.state_mgr = StateManager(self.workspace)
        self.git.init_repo()
        setup_file_logging(self.workspace, self.log)
        #     Agent
        self.planner = Agent("Planner", PLANNER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
        self.sprint_planner = Agent("SprintPlanner", SPRINT_PLANNER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
        self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS, use_state=True, logger=self.log)
        self.evaluator = Agent("Evaluator", EVALUATOR_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, use_state=True, logger=self.log)
        #    ?        self.score_history: list[float] = []
        self.sprint_score_history: list[float] = []
        self.overall_score_history: list[float] = []
        self.commit_history: list[tuple[int, str]] = []
        self.strategy_history: list[dict] = []
        self.token_totals: dict[str, int] = {"prompt": 0, "completion": 0}
        self.round_stats: list[dict] = []
        self._completed_rounds: int = 0
        self._resumed: bool = False
        # P2     ?        self.eval_cache = EvalCache(str(self.workspace))
        self.dashboard = Dashboard(str(self.workspace))
        self._load_state()
    # ------------------------------------------------------------------ #
    #              ?StateManager ?                                  #
    # ------------------------------------------------------------------ #
    def _save_state(self) -> None:
        """Persist mutable harness state after each round."""
        state = {
            "completed_rounds": self._completed_rounds,
            "score_history": self.score_history,
            "sprint_score_history": self.sprint_score_history,
            "overall_score_history": self.overall_score_history,
            "strategy_history": self.strategy_history,
            "token_totals": self.token_totals,
            "round_stats": self.round_stats,
            "dashboard": self.dashboard.state.to_dict() if hasattr(self, 'dashboard') else {},
        }
        self.state_mgr.save(state)
    def _load_state(self) -> None:
        """Load persisted state if exists."""
        state = self.state_mgr.load()
        if state is None:
            return
        self._completed_rounds = int(state.get("completed_rounds", 0))
        self.score_history = [float(s) for s in state.get("score_history", [])]
        self.sprint_score_history = [float(s) for s in state.get("sprint_score_history", [])]
        self.overall_score_history = [float(s) for s in state.get("overall_score_history", [])]
        if not self.sprint_score_history and self.score_history:
            self.sprint_score_history = list(self.score_history)
        if not self.overall_score_history and self.score_history:
            self.overall_score_history = list(self.score_history)
        self.strategy_history = state.get("strategy_history", [])
        self.token_totals = state.get("token_totals", {"prompt": 0, "completion": 0})
        self.round_stats = state.get("round_stats", [])
        self._resumed = True
        last_overall = self.overall_score_history[-1] if self.overall_score_history else 'n/a'
        self.log.info(
            f"[state] Resumed from {config.STATE_FILE}  ?"
            f"{self._completed_rounds} round(s) already completed, "
            f"last overall: {last_overall}"
        )
    def _clear_state(self) -> None:
        """Remove STATE_FILE after a successful run."""
        self.state_mgr.clear()
    # ------------------------------------------------------------------ #
    #      ?                                                           #
    # ------------------------------------------------------------------ #
    def run(self, user_prompt: str) -> dict:
        """Run the full Planner  ?Contract  ?Build-Evaluate loop."""
        self.log.info("="*60)
        self.log.info("Harness %s", "resuming" if self._resumed else "starting")
        self.log.info("="*60)
        self.log.info(f"Workspace: {self.workspace}")
        self.log.info(f"Prompt: {user_prompt}")
        if self._resumed:
            last_sprint = self.sprint_score_history[-1] if self.sprint_score_history else 'n/a'
            last_overall = self.overall_score_history[-1] if self.overall_score_history else 'n/a'
            self.log.info(
                f"Resuming from round {self._completed_rounds + 1} "
                f"(completed: {self._completed_rounds}, "
                f"last sprint: {last_sprint}, last overall: {last_overall})"
            )
        spec_path = self.workspace / config.SPEC_FILE
        contract_path = self.workspace / config.CONTRACT_FILE
        # Phase 1: Plan
        if spec_path.exists():
            self.log.info("Phase 1: Plan  ?SKIPPED (spec.md already exists)")
        else:
            self.log.info("\n" + "="*60)
            self.log.info("Phase 1: Plan")
            self.log.info("="*60)
            self.planner.run(
                f"Create a product specification for:\n\n{user_prompt}\n\n"
                f"Save to {config.SPEC_FILE}"
            )
            if not spec_path.exists():
                self.log.error("Planner failed to create spec.md")
                return {"success": False, "error": "Planner failed to create spec", "rounds": 0, "score": 0}
            self.log.info("Spec created successfully")
        # Phase 2: Contract
        if contract_path.exists():
            self.log.info("Phase 2: Contract  ?SKIPPED (contract.md already exists)")
        else:
            self.log.info("\n" + "="*60)
            self.log.info("Phase 2: Contract")
            self.log.info("="*60)
            negotiate_contract(self.workspace, self.log)
        # Phase 3+: Build-Evaluate loop
        self.dashboard.start_run()
        start_round = self._completed_rounds + 1
        for round_num in range(start_round, config.MAX_ROUNDS + 1):
            self.log.info("\n" + "="*60)
            self.log.info(f"Round {round_num}/{config.MAX_ROUNDS}")
            self.log.info("="*60)
            self.dashboard.start_round(round_num)
            score = self._build_round(round_num)
            self._completed_rounds = round_num
            self._save_state()
            if score >= config.PASS_THRESHOLD:
                self.log.info(f"\nSuccess! Final overall score: {score}")
                log_final_stats(self.log, self.round_stats, self.sprint_score_history,
                               self.overall_score_history, self.token_totals)
                self._clear_state()
                self.dashboard.end_run(success=True)
                return {
                    "success": True, "score": score, "rounds": round_num,
                    "token_totals": dict(self.token_totals),
                    "round_stats": list(self.round_stats),
                }
            if round_num < config.MAX_ROUNDS:
                self.log.info(f"Overall score {score:.1f} below threshold {config.PASS_THRESHOLD}, continuing...")
        log_final_stats(self.log, self.round_stats, self.sprint_score_history,
                       self.overall_score_history, self.token_totals)
        final_score = self.overall_score_history[-1] if self.overall_score_history else 0
        self.dashboard.end_run(success=False)
        return {
            "success": False,
            "score": final_score,
            "rounds": len(self.overall_score_history),
            "token_totals": dict(self.token_totals),
            "round_stats": list(self.round_stats),
        }
    # ------------------------------------------------------------------ #
    #      Build-Evaluate                                               #
    # ------------------------------------------------------------------ #
    def _build_round(self, round_num: int) -> float:
        """      ?Build-Evaluate"""
        round_start = time.time()
        rollback_msg = ""
        # comment removed
        if self.sprint_score_history and self.sprint_score_history[-1] < config.SPRINT_PASS_THRESHOLD:
            latest_hash = self.git.get_head_hash()
            if latest_hash:
                self.git.rollback_to(
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
        #    ?2: Overall       ?        elif self.overall_score_history and len(self.overall_score_history) >= 2:
            best_idx = max(range(len(self.overall_score_history)), key=lambda i: self.overall_score_history[i])
            best_overall = self.overall_score_history[best_idx]
            last_overall = self.overall_score_history[-1]
            if last_overall < best_overall - config.SIGNIFICANT_DROP:
                best_hash = self.git.get_commit_for_round(best_idx + 1)
                if best_hash:
                    self.git.rollback_to(
                        best_hash,
                        f"Overall dropped {last_overall:.1f}  ?best was {best_overall:.1f} at round {best_idx + 1}"
                    )
                    rollback_msg = (
                        f"\nNOTE: Overall score dropped to {last_overall:.1f}. "
                        f"Rolled back to round {best_idx + 1} (best overall {best_overall:.1f}). "
                        f"The last approach broke existing functionality. Fix or change strategy."
                    )
        # Sprint    
        plan_sprint(self.workspace, round_num, self.sprint_planner, self.log)
        negotiate_sprint_contract(self.workspace, round_num, self.log)
        # Build
        self.log.info("Build phase")
        self.dashboard.start_agent("Builder")
        build_task = build_build_task(
            self.workspace, round_num, rollback_msg,
            self.overall_score_history, self.strategy_history
        )
        build_result, build_usage = self.builder.run_with_stats(build_task)
        self.dashboard.end_agent("success")
        #       
        strategy = parse_strategy(build_result)
        self.strategy_history.append(strategy)
        self.log.info(
            f"Builder strategy: {strategy['strategy']}"
            + (f"  ?{strategy['reason']}" if strategy['reason'] else "")
        )
        if strategy['strategy'] == 'PIVOT' and strategy.get('new_direction'):
            self.log.info(f"  New direction: {strategy['new_direction']}")
        # Dev Server    
        server_ok, server_msg = verify_dev_server(self.workspace)
        if not server_ok:
            self.log.warning(f"[build_gate] Dev server verification failed: {server_msg}")
            self.dashboard.add_alert(f"Round {round_num}: Dev server failed - {server_msg}")
        # Git    
        self.git.commit_round(round_num)
        #              
        ws_state_path = self.workspace / ".workspace_state.json"
        if ws_state_path.exists():
            try:
                ws_data = json.loads(ws_state_path.read_text(encoding="utf-8"))
                if ws_data.get("last_build_status") == "error":
                    self.log.warning("[build_gate] Build failed  ?skipping evaluation and forcing retry")
                    self.dashboard.add_alert(f"Build failed in round {round_num}, skipping eval")
                    score = config.SPRINT_PASS_THRESHOLD - 0.5
                    self.sprint_score_history.append(score)
                    self.overall_score_history.append(score)
                    self.score_history.append(score)
                    round_prompt = build_usage["prompt"]
                    round_completion = build_usage["completion"]
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
                    log_round_stats(self.log, round_num, score, score, score,
                                   round_prompt, round_completion, elapsed)
                    self.dashboard.update_scores(score, score)
                    self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])
                    return score
            except Exception as e:
                self.log.debug(f"[build_gate] Could not check build status: {e}")
        # Evaluate
        sprint_contract_path = self.workspace / config.SPRINT_CONTRACT_FILE
        contract_ref = config.SPRINT_CONTRACT_FILE if sprint_contract_path.exists() else config.CONTRACT_FILE
        # Step 1 & 2: Code Review + Browser Test (parallel)
        self.log.info("Evaluate phase  ?Step 1 & 2: Code Review + Browser Test (parallel)")
        self.dashboard.start_agent("CodeReviewer+BrowserTester")
        code_reviewer = Agent("CodeReviewer", CODE_REVIEWER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
        browser_tester = Agent("BrowserTester", BROWSER_TESTER_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, logger=self.log)
        code_review_result, code_review_usage, browser_result, browser_usage = self._run_eval_parallel(
            code_reviewer, browser_tester, contract_ref
        )
        self.dashboard.end_agent("success")
        # Step 3: Scoring
        self.log.info("Evaluate phase  ?Step 3: Scoring")
        self.dashboard.start_agent("Evaluator")
        eval_task = self.eval_cache.build_evaluator_prompt(
            round_num=round_num,
            code_review_result=code_review_result,
            browser_result=browser_result,
            contract_ref=contract_ref,
            previous_rounds=2,
        )
        eval_result, eval_usage = self.evaluator.run_with_stats(eval_task)
        self.dashboard.end_agent("success")
        #     feedback.md
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = eval_result
        else:
            eval_text = eval_result
        sprint_score, overall_score = parse_scores(eval_text)
        self.sprint_score_history.append(sprint_score)
        self.overall_score_history.append(overall_score)
        self.score_history.append(overall_score)
        self.log.info(f"  Sprint score: {sprint_score:.1f}/10 | Overall score: {overall_score:.1f}/10")
        #       
        dim_scores = parse_dimension_scores(eval_text)
        if dim_scores:
            for dim, s in sorted(dim_scores.items()):
                threshold = config.DIMENSION_THRESHOLDS.get(dim, 0)
                status = "OK" if s >= threshold else "FAIL"
                self.log.info(f"  [{status}] {dim}: {s}/10 (threshold {threshold})")
        else:
            self.log.warning("Could not parse per-dimension scores from evaluator feedback")
        #        ?        score = overall_score
        failed_dims = check_dimension_thresholds(dim_scores)
        if failed_dims:
            self.log.warning(f"Hard threshold(s) failed: {', '.join(failed_dims)}")
            if score >= config.PASS_THRESHOLD:
                self.log.warning(
                    f"Overall score {score} would have passed, but dimension hard threshold "
                    f"forces continuation. Effective score capped to {config.PASS_THRESHOLD - 0.1}."
                )
                score = config.PASS_THRESHOLD - 0.1
        round_prompt = (
            build_usage["prompt"] + code_review_usage["prompt"] +
            browser_usage["prompt"] + eval_usage["prompt"]
        )
        round_completion = (
            build_usage["completion"] + code_review_usage["completion"] +
            browser_usage["completion"] + eval_usage["completion"]
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
        log_round_stats(self.log, round_num, score, sprint_score, overall_score,
                       round_prompt, round_completion, elapsed)
        self.dashboard.update_scores(sprint_score, overall_score)
        self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])
        return score
    def _run_eval_parallel(
        self,
        code_reviewer: Agent,
        browser_tester: Agent,
        contract_ref: str,
    ) -> tuple[str, dict, str, dict]:
        """"""