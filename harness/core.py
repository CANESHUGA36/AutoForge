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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
import config
from agents import Agent
from dashboard import Dashboard
from eval_cache import EvalCache
from prompts import (
    ARCHITECT_SYSTEM, BUILDER_SYSTEM, REVIEWER_SYSTEM, JUDGE_SYSTEM, SPRINT_MASTER_SYSTEM,
)
from tools_impl import TOOL_SCHEMAS, BROWSER_TOOL_SCHEMAS
from harness.build import build_build_task, verify_dev_server
from harness.eval import parse_scores, parse_dimension_scores, check_dimension_thresholds
from harness.git import GitManager
from harness.logging import setup_file_logging, log_round_stats, log_final_stats
from harness.sprint import plan_sprint_master
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
        #     Agent（按职能细分工具集）
        CORE_TOOLS = {"read_file", "write_file", "read_skill_file"}
        FILE_TOOLS = {"edit_file", "list_files"}
        EXEC_TOOLS = {"run_bash", "start_dev_server"}
        BROWSER_TOOLS = {"browser_test", "browser_evaluate"}
        GEN_TOOLS = {"generate_image", "search_web", "analyze_image"}
        META_TOOLS = {"validate_build", "project_init", "delegate_task"}

        architect_tools = CORE_TOOLS | GEN_TOOLS | {"search_web"}
        sprint_master_tools = CORE_TOOLS | FILE_TOOLS | {"list_files"}
        builder_tools = CORE_TOOLS | FILE_TOOLS | EXEC_TOOLS | GEN_TOOLS | META_TOOLS
        reviewer_tools = CORE_TOOLS | FILE_TOOLS | BROWSER_TOOLS | {"start_dev_server"}
        judge_tools = CORE_TOOLS | {"read_file", "write_file", "read_skill_file"}

        self.architect = Agent("Architect", ARCHITECT_SYSTEM, TOOL_SCHEMAS, allowed_tools=architect_tools, logger=self.log)
        self.sprint_master = Agent("SprintMaster", SPRINT_MASTER_SYSTEM, TOOL_SCHEMAS, allowed_tools=sprint_master_tools, logger=self.log)
        self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS, use_state=True, allowed_tools=builder_tools, logger=self.log)
        self.reviewer = Agent("Reviewer", REVIEWER_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, allowed_tools=reviewer_tools, logger=self.log)
        self.judge = Agent("Judge", JUDGE_SYSTEM, TOOL_SCHEMAS, allowed_tools=judge_tools, logger=self.log)
        self.score_history: list[float] = []
        self.sprint_score_history: list[float] = []
        self.overall_score_history: list[float] = []
        self.commit_history: list[tuple[int, str]] = []
        self.strategy_history: list[dict] = []
        self.token_totals: dict[str, int] = {"prompt": 0, "completion": 0}
        self.round_stats: list[dict] = []
        self._completed_rounds: int = 0
        self._resumed: bool = False
        self.eval_cache = EvalCache(str(self.workspace))
        self.dashboard = Dashboard(str(self.workspace), logger=self.log)
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
        """Run the full Architect -> Build-Evaluate loop."""
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
        # Phase 1: Design (Architect) — spec.md + contract.md 一次性产出
        if spec_path.exists() and contract_path.exists():
            self.log.info("Phase 1: Design  ?SKIPPED (spec.md and contract.md already exist)")
        else:
            self.log.info("\n" + "="*60)
            self.log.info("Phase 1: Design")
            self.log.info("="*60)
            self.architect.run(
                f"Create a product specification and acceptance criteria for:\n\n{user_prompt}\n\n"
                f"First save the spec to {config.SPEC_FILE}, then save the contract to {config.CONTRACT_FILE}."
            )
            if not spec_path.exists():
                self.log.error("Architect failed to create spec.md")
                return {"success": False, "error": "Architect failed to create spec", "rounds": 0, "score": 0}
            if not contract_path.exists():
                self.log.error("Architect failed to create contract.md")
                return {"success": False, "error": "Architect failed to create contract", "rounds": 0, "score": 0}
            self.log.info("Spec and contract created successfully")
        # Phase 2+: Build-Evaluate loop
        self.dashboard.start_run()
        start_round = self._completed_rounds + 1
        for round_num in range(start_round, getattr(config, 'MAX_ROUNDS_HARD', 10) + 1):
            # FIX BUG #9: Recalculate max_rounds each round so runtime adjustments apply
            max_rounds = self._calculate_max_rounds()
            if round_num > max_rounds:
                self.log.info(f"Stopping at round {round_num-1} (dynamic max_rounds={max_rounds})")
                break
            self.log.info("\n" + "="*60)
            self.log.info(f"Round {round_num}/{max_rounds}")
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
            if round_num < max_rounds:
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
        # Condition 2: Overall score dropped significantly from best
        elif self.overall_score_history and len(self.overall_score_history) >= 2:
            best_idx = max(range(len(self.overall_score_history)), key=lambda i: self.overall_score_history[i])
            best_overall = self.overall_score_history[best_idx]
            last_overall = self.overall_score_history[-1]
            if last_overall < best_overall - config.SIGNIFICANT_DROP:
                best_hash = self.git.get_commit_for_round(best_idx + 1)
                if best_hash:
                    self.git.rollback_to(
                        best_hash,
                        f"Overall dropped {last_overall:.1f} — best was {best_overall:.1f} at round {best_idx + 1}"
                    )
                    rollback_msg = (
                        f"\nNOTE: Overall score dropped to {last_overall:.1f}. "
                        f"Rolled back to round {best_idx + 1} (best overall {best_overall:.1f}). "
                        f"The last approach broke existing functionality. Fix or change strategy."
                    )
        # Sprint
        sprint_ok = plan_sprint_master(self.workspace, round_num, self.sprint_master, self.log)
        if not sprint_ok:
            self.log.warning(f"[sprint] Round {round_num} using fallback sprint.md")
        # Build
        self.log.info("Build phase")
        self.dashboard.start_agent("Builder")
        build_task = self._build_build_task(round_num, rollback_msg)
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
        # FIX BUG #1: Build Gate BEFORE commit — prevent bad code from being committed
        ws_state_path = self.workspace / ".workspace_state.json"
        build_failed = False
        if ws_state_path.exists():
            try:
                ws_data = json.loads(ws_state_path.read_text(encoding="utf-8"))
                if ws_data.get("last_build_status") == "error":
                    build_failed = True
            except Exception as e:
                self.log.debug(f"[build_gate] Could not check build status: {e}")
        
        if build_failed:
            self.log.warning("[build_gate] Build failed — skipping commit and evaluation, forcing retry")
            self.dashboard.add_alert(f"Build failed in round {round_num}, skipping eval")
            # Rollback any uncommitted changes to keep workspace clean
            self.git.rollback_to(
                self.git.get_head_hash(),
                f"Build failed in round {round_num} — rolling back uncommitted changes"
            )
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
        
        # Git commit only after build gate passes
        self.git.commit_round(round_num)
        # Evaluate
        contract_ref = config.CONTRACT_FILE
        # Step 1: Reviewer（统一审查报告）
        self.log.info("Evaluate phase  ?Step 1: Reviewer (unified review)")
        self.dashboard.start_agent("Reviewer")
        review_task = (
            f"Review the codebase and test the web app for round {round_num}.\n"
            f"Read the acceptance criteria in {contract_ref}, then:\n"
            f"1. Examine the MOST IMPORTANT source files (max 8 files).\n"
            f"2. Start the dev server and run browser tests (desktop + mobile).\n"
            f"3. Produce a unified review report.\n"
            f"Limit: 10 iterations max."
        )
        review_result, review_usage = self.reviewer.run_with_stats(review_task)
        self.eval_cache.save_round(round_num, review_result)
        self.dashboard.end_agent("success")
        # Step 2: Judge（评分）
        self.log.info("Evaluate phase  ?Step 2: Judge (scoring)")
        self.dashboard.start_agent("Judge")
        # FIX: Judge reads Reviewer report from EvalCache (not hardcoded path)
        review_report = self.eval_cache.get_full_report(round_num) or ""
        review_hint = ""
        if review_report:
            review_hint = f"\n\nReviewer report for this round:\n{review_report[:4000]}\n\n"
        judge_task = (
            f"Round {round_num} evaluation.\n\n"
            f"Read {config.SPRINT_FILE}, {config.CONTRACT_FILE}, "
            f"and any previous {config.FEEDBACK_FILE}, then produce feedback.md with scores."
            f"{review_hint}"
        )
        judge_result, judge_usage = self.judge.run_with_stats(judge_task)
        self.dashboard.end_agent("success")
        #     feedback.md
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = judge_result
        else:
            eval_text = judge_result
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
            self.log.warning("Could not parse per-dimension scores from judge feedback")
        score = overall_score
        failed_dims = check_dimension_thresholds(dim_scores)
        if failed_dims:
            self.log.warning(f"Hard threshold(s) failed: {', '.join(failed_dims)}")
            if score >= config.PASS_THRESHOLD:
                self.log.warning(
                    f"Overall score {score} would have passed, but dimension hard threshold "
                    f"forces continuation. Effective score capped to {config.PASS_THRESHOLD - 0.1}."
                )
                score = config.PASS_THRESHOLD - 0.1
                # FIX BUG #2: Sync overall_score so downstream logic sees the capped value
                overall_score = score
        round_prompt = build_usage["prompt"] + review_usage["prompt"] + judge_usage["prompt"]
        round_completion = build_usage["completion"] + review_usage["completion"] + judge_usage["completion"]
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
        
        # Round budget summary
        self.log.info(
            f"[round_budget] Round {round_num} complete | "
            f"elapsed: {elapsed:.0f}s | "
            f"tokens: {round_prompt}p+{round_completion}c | "
            f"total: {self.token_totals['prompt']}p+{self.token_totals['completion']}c"
        )
        # FIX BUG #7: Release ALL browser instances to prevent resource leak
        try:
            from tools.playwright_mcp import close_mcp_bridge
            close_mcp_bridge(None)  # None = close all contexts
        except Exception as e:
            self.log.debug(f"[playwright] Could not release bridge: {e}")
        return score

    def _build_build_task(self, round_num: int, rollback_msg: str) -> str:
        """Build the task prompt for the Builder agent."""
        sprint_path = self.workspace / config.SPRINT_FILE
        contract_path = self.workspace / config.CONTRACT_FILE
        feedback_path = self.workspace / config.FEEDBACK_FILE

        # Primary guide: sprint.md (自带验收标准)
        if sprint_path.exists():
            primary_guide = f"1. Read {config.SPRINT_FILE} — this is your ONLY task list and acceptance criteria for this round.\n"
        else:
            primary_guide = f"1. Read {config.SPEC_FILE} for product spec.\n"

        # Global contract as fallback
        if contract_path.exists():
            primary_guide += f"2. Read {config.CONTRACT_FILE} for global acceptance criteria.\n"

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

        # 动态注入迭代预算
        task = self._inject_iteration_budget(task)

        task += "\n\nCommit with git when done."
        return task

    def _build_trend_summary(self) -> str:
        """将完整历史分数压缩为趋势摘要"""
        if not self.overall_score_history:
            return "No scores yet."
        recent = self.overall_score_history[-5:]
        trend = " -> ".join(f"{s:.1f}" for s in recent)
        best_idx = max(range(len(self.overall_score_history)), key=lambda i: self.overall_score_history[i])
        best_round = best_idx + 1
        best_score = self.overall_score_history[best_idx]
        last_strategy = self.strategy_history[-1]["strategy"] if self.strategy_history else "UNKNOWN"
        consecutive_refine = 0
        for s in reversed(self.strategy_history):
            if s["strategy"] == "REFINE":
                consecutive_refine += 1
            else:
                break
        return (
            f"Score Trend: {trend}\n"
            f"Best Round: Round {best_round} ({best_score:.1f})\n"
            f"Last Strategy: {last_strategy}\n"
            f"Consecutive REFINEs: {consecutive_refine}"
        )

    def _inject_iteration_budget(self, build_task: str) -> str:
        """从 sprint.md 解析预估迭代数，动态注入预算提示。"""
        sprint_path = self.workspace / config.SPRINT_FILE
        if not sprint_path.exists():
            return build_task

        text = sprint_path.read_text(encoding="utf-8", errors="replace")
        import re
        # FIX BUG #11: Support multiple formats (Chinese, English, colon variants)
        patterns = [
            r'[保守|Conservative|conservative][：:]\s*(\d+)\s*[次|iterations|]',
            r'(\d+)\s*[次|iterations]\s*\(?[保守|conservative]\)?',
            r'预算[:：]\s*(\d+)',
            r'budget[:：]\s*(\d+)',
        ]
        conservative = 25
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                conservative = int(match.group(1))
                break

        threshold = int(conservative * 0.8)
        budget_msg = f"""
## Iteration Budget
本轮保守预算：{conservative} 次迭代。
如果已使用 >{threshold} 次，停止添加新功能，优先保证验收标准中优先级最高的 2 条。
"""
        return build_task + budget_msg

    # ------------------------------------------------------------------ #
    #      Dynamic Round Limits                                         #
    # ------------------------------------------------------------------ #
    def _calculate_max_rounds(self) -> int:
        """基于项目复杂度、历史表现、Builder 策略动态调整轮数上限"""
        base = self._estimate_from_spec()
        runtime_adjust = self._runtime_adjustment()
        strategy_adjust = self._strategy_adjustment()

        max_rounds = min(base + runtime_adjust + strategy_adjust, getattr(config, 'MAX_ROUNDS_HARD', 10))
        max_rounds = max(max_rounds, getattr(config, 'MIN_ROUNDS', 3))

        self.log.info(f"[dynamic_rounds] base={base}, runtime={runtime_adjust}, "
                      f"strategy={strategy_adjust} -> max_rounds={max_rounds}")
        return max_rounds

    def _estimate_from_spec(self) -> int:
        """从 spec.md 解析功能点，估算基础轮数"""
        spec_path = self.workspace / config.SPEC_FILE
        if not spec_path.exists():
            return 5

        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        feature_lines = [l for l in spec_text.splitlines()
                         if l.strip().startswith("-") and "feature" in l.lower()]
        feature_count = len(feature_lines)
        asset_count = spec_text.count("generate_image")

        rounds = 2  # 骨架 + 验收
        rounds += feature_count // 3
        rounds += asset_count // 2
        return min(rounds, 8)

    def _runtime_adjustment(self) -> int:
        """基于已跑轮次表现，追加或缩减"""
        if not self.sprint_score_history:
            return 0

        # 信号 A：连续高分但 Overall 没过 -> 功能多，需要更多轮
        recent = self.sprint_score_history[-2:]
        if len(recent) == 2 and all(s >= 8.0 for s in recent):
            if not self.overall_score_history or self.overall_score_history[-1] < config.PASS_THRESHOLD:
                return 2

        # 信号 B：连续停滞（Overall 几乎不变）-> 缩减，逼迫 PIVOT
        if len(self.overall_score_history) >= 3:
            last_three = self.overall_score_history[-3:]
            if max(last_three) - min(last_three) < 0.5:
                return -1

        # 信号 C：Overall 持续上升且已接近门槛 -> 追加 1 轮冲刺
        if self.overall_score_history and self.overall_score_history[-1] >= 6.5:
            return 1

        return 0

    def _strategy_adjustment(self) -> int:
        """Builder 策略信号"""
        if not self.strategy_history:
            return 0

        recent_strategies = [s["strategy"] for s in self.strategy_history[-2:]]

        # 连续 PIVOT：架构在重构，给额外轮次
        if recent_strategies == ["PIVOT", "PIVOT"]:
            return 2

        # 连续 REFINE 且分数上升：势头好，给 1 轮奖励
        if recent_strategies == ["REFINE", "REFINE"]:
            if len(self.overall_score_history) >= 2 and \
               self.overall_score_history[-1] > self.overall_score_history[-2]:
                return 1

        return 0
