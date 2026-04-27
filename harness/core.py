#!/usr/bin/env python3
"""
Harness        ?
    Anthropic     "Harness design for long-running application development"
"""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
import config
from agents import Agent
from dashboard import Dashboard
from eval_cache import EvalCache
from prompts import (
    ARCHITECT_SYSTEM, BUILDER_SYSTEM, REVIEWER_SYSTEM, JUDGE_SYSTEM, SPRINT_MASTER_SYSTEM,
)
from tools_impl import TOOL_SCHEMAS, BROWSER_TOOL_SCHEMAS
from harness.build import build_build_task
from harness.eval import parse_pass_rates, parse_skip_rate, compute_actual_contract_rate, parse_group_pass_rates
from harness.git import GitManager
from harness.feature_groups import (
    FeatureGroupState, parse_feature_groups, get_group_instruction,
    TIER_REQUIREMENTS, OVERALL_PASS_THRESHOLD,
)
from harness.logging import setup_file_logging, log_round_stats, log_final_stats
from harness.sprint import plan_sprint_master
from harness.state import StateManager
from harness.strategy import parse_strategy
from harness.events import EventBus
from harness.pipeline import PipelineRunner
from harness.stages import (
    PreBuildGateStage, BuildGateStage, DevServerGateStage,
    ScreenshotGateStage, GitCommitStage,
)
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
        self.sprint_pass_rate_history: list[float] = []
        self.contract_pass_rate_history: list[float] = []
        self.commit_history: list[tuple[int, str]] = []
        self.strategy_history: list[dict] = []
        self.token_totals: dict[str, int] = {"prompt": 0, "completion": 0}
        self.round_stats: list[dict] = []
        self._completed_rounds: int = 0
        self._resumed: bool = False
        self._last_sprint_passed: bool = False
        self.eval_cache = EvalCache(str(self.workspace))
        self.dashboard = Dashboard(str(self.workspace), logger=self.log)
        self.event_bus = EventBus(self.workspace)
        self.dashboard.subscribe_to(self.event_bus)
        # Feature-group state machine (populated on first _build_round)
        self.feature_groups: FeatureGroupState | None = None
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
            "sprint_pass_rate_history": self.sprint_pass_rate_history,
            "contract_pass_rate_history": self.contract_pass_rate_history,
            "strategy_history": self.strategy_history,
            "token_totals": self.token_totals,
            "round_stats": self.round_stats,
            "last_sprint_passed": self._last_sprint_passed,
            "dashboard": self.dashboard.state.to_dict() if hasattr(self, 'dashboard') else {},
            "pipeline_state": {
                "version": 1,
                "last_round": self._completed_rounds,
                "event_log": str(self.workspace / ".events" / "pipeline.jsonl"),
            },
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
        self.sprint_pass_rate_history = [float(s) for s in state.get("sprint_pass_rate_history", [])]
        self.contract_pass_rate_history = [float(s) for s in state.get("contract_pass_rate_history", [])]
        # Migrate from legacy 0-10 scores to pass rates if needed
        if not self.sprint_pass_rate_history and self.sprint_score_history:
            max_val = max(self.sprint_score_history) if self.sprint_score_history else 0
            self.sprint_pass_rate_history = [
                (s / 10.0 if max_val > 1.0 else s) for s in self.sprint_score_history
            ]
        if not self.contract_pass_rate_history and self.overall_score_history:
            max_val = max(self.overall_score_history) if self.overall_score_history else 0
            self.contract_pass_rate_history = [
                (s / 10.0 if max_val > 1.0 else s) for s in self.overall_score_history
            ]
        if not self.sprint_score_history and self.score_history:
            self.sprint_score_history = list(self.score_history)
        if not self.overall_score_history and self.score_history:
            self.overall_score_history = list(self.score_history)
        self.strategy_history = state.get("strategy_history", [])
        self.token_totals = state.get("token_totals", {"prompt": 0, "completion": 0})
        self.round_stats = state.get("round_stats", [])
        self._last_sprint_passed = state.get("last_sprint_passed", False)
        # Load pipeline state (for forward compatibility)
        pipeline_state = state.get("pipeline_state", {})
        if pipeline_state:
            self.log.debug(f"[state] Pipeline state v{pipeline_state.get('version', '?')}")
        self._resumed = True
        last_overall = self.overall_score_history[-1] if self.overall_score_history else 'n/a'
        last_contract = self.contract_pass_rate_history[-1] if self.contract_pass_rate_history else 'n/a'
        self.log.info(
            f"[state] Resumed from {config.STATE_FILE} — "
            f"{self._completed_rounds} round(s) already completed, "
            f"last overall: {last_overall}, last contract rate: {last_contract}"
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
            last_contract = self.contract_pass_rate_history[-1] if self.contract_pass_rate_history else 'n/a'
            self.log.info(
                f"Resuming from round {self._completed_rounds + 1} "
                f"(completed: {self._completed_rounds}, "
                f"last sprint: {last_sprint}, last overall: {last_overall}, "
                f"last contract rate: {last_contract})"
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
            result = self._build_round(round_num)
            self._completed_rounds = round_num
            self._save_state()

            sprint_rate = result.get("sprint_rate", 0.0)
            contract_rate = result.get("contract_rate", 0.0)
            score = result.get("score", 0.0)

            # Feature-group based exit condition
            exit_ok, exit_reason = self._check_exit_condition()
            if exit_ok:
                self.log.info(f"\nSuccess! {exit_reason}")
                log_final_stats(self.log, self.round_stats, self.sprint_score_history,
                               self.overall_score_history, self.token_totals)
                self._clear_state()
                self.dashboard.end_run(success=True)
                return {
                    "success": True, "score": score, "rounds": round_num,
                    "sprint_rate": sprint_rate, "contract_rate": contract_rate,
                    "token_totals": dict(self.token_totals),
                    "round_stats": list(self.round_stats),
                }
            if round_num < max_rounds:
                self.log.info(
                    f"{exit_reason} — continuing..."
                )
        log_final_stats(self.log, self.round_stats, self.sprint_score_history,
                       self.overall_score_history, self.token_totals)
        final_score = self.overall_score_history[-1] if self.overall_score_history else 0
        final_contract = self.contract_pass_rate_history[-1] if self.contract_pass_rate_history else 0
        self.dashboard.end_run(success=False)
        return {
            "success": False,
            "score": final_score,
            "contract_rate": final_contract,
            "rounds": len(self.overall_score_history),
            "token_totals": dict(self.token_totals),
            "round_stats": list(self.round_stats),
        }
    # ------------------------------------------------------------------ #
    #      Build-Evaluate                                               #
    # ------------------------------------------------------------------ #
    def _build_round(self, round_num: int) -> dict:
        """使用 Pipeline 架构执行一轮 Build-Evaluate。返回 dict 供双轨评分使用。"""
        round_start = time.time()

        # Initialize feature-group state on first round
        if self.feature_groups is None:
            contract_path = self.workspace / config.CONTRACT_FILE
            if contract_path.exists():
                contract_text = contract_path.read_text(encoding="utf-8", errors="replace")
                groups = parse_feature_groups(contract_text)
                self.feature_groups = FeatureGroupState(groups)
                self.log.info(
                    f"[feature_groups] Initialized {len(groups)} groups, "
                    f"starting with {self.feature_groups.current_group_id}"
                )
            else:
                self.log.warning("[feature_groups] contract.md not found, falling back to legacy mode")

        rollback_msg = self._prepare_rollback_msg()

        # Sprint — feature-group driven (task_limit no longer needed)
        group_hint = ""
        if self.feature_groups and self.feature_groups.current_group:
            cg = self.feature_groups.current_group
            group_hint = (
                f"当前功能组: {cg['id']} — {cg['name']} "
                f"({len(cg['criteria'])} 项标准)"
            )
            self.log.info(f"[sprint] Round {round_num} | {group_hint}")
        else:
            task_limit = self._calculate_sprint_task_limit()
            budget_hint = self._get_builder_budget_hint()
            self.log.info(f"[sprint] Round {round_num} task_limit={task_limit} | {budget_hint}")

        sprint_ok = plan_sprint_master(
            self.workspace, round_num, self.sprint_master, self.log,
            task_limit=1, budget_hint=self._get_builder_budget_hint(),
            group_hint=group_hint,
        )
        if not sprint_ok:
            self.log.warning(f"[sprint] Round {round_num} using fallback sprint.md")

        # FIX: Clear stale build status from previous round before Builder starts
        ws_state_path = self.workspace / ".workspace_state.json"
        if ws_state_path.exists():
            try:
                ws_data = json.loads(ws_state_path.read_text(encoding="utf-8"))
                if ws_data.get("last_build_status") == "error":
                    self.log.info("[build_gate] Clearing stale error status from previous round")
                    ws_data["last_build_status"] = "unknown"
                    ws_state_path.write_text(json.dumps(ws_data), encoding="utf-8")
            except Exception:
                pass

        # ===== Pipeline Phase 1: 环境预检（仅在需要时）=====
        if round_num == 1 or self._needs_env_check():
            self.log.info("[pipeline] Phase 1 — Environment check")
            env_runner = PipelineRunner(self.workspace, self.event_bus)
            env_runner.add_stage(PreBuildGateStage)
            env_ctx = env_runner.run(round_num)
            prebuild = env_ctx.get("prebuild_gate")
            if prebuild and not prebuild.success:
                self.log.error(f"[prebuild_gate] Environment check failed: {prebuild.message}")
                self.dashboard.add_alert(f"Round {round_num}: env check failed")
                return self._handle_pipeline_failure(
                    round_num, round_start, "prebuild_gate",
                    prebuild.message, 0.0
                )

        # ===== Builder Agent（代码编写）=====
        self.log.info("Build phase")
        self.dashboard.start_agent("Builder")
        build_task = self._build_build_task(round_num, rollback_msg)
        build_result_text, build_usage = self.builder.run_with_stats(build_task)
        self.dashboard.end_agent("success")

        strategy = parse_strategy(build_result_text)
        self.strategy_history.append(strategy)
        self.log.info(
            f"Builder strategy: {strategy['strategy']}"
            + (f"  ?{strategy['reason']}" if strategy['reason'] else "")
        )
        if strategy['strategy'] == 'PIVOT' and strategy.get('new_direction'):
            self.log.info(f"  New direction: {strategy['new_direction']}")

        # FIX: Force restart dev server to ensure Builder's code changes are loaded.
        # Next.js Turbopack may not hot-reload files written by Python subprocess
        # (especially new files or files modified in a previous round). Killing the
        # dev server before validation forces a clean start from the latest source.
        from tools_impl import _kill_dev_server
        self.log.info("[dev_server] Stopping existing dev server before validation...")
        _kill_dev_server()
        time.sleep(2)  # Wait for port release

        # ===== Pipeline Phase 2: 验证 + 提交 =====
        self.log.info("[pipeline] Phase 2 — Validation & commit")
        runner = PipelineRunner(self.workspace, self.event_bus)
        runner.add_stage(BuildGateStage)
        runner.add_stage(DevServerGateStage)
        runner.add_stage(ScreenshotGateStage)
        runner.add_stage(GitCommitStage)
        context = runner.run(round_num)

        # 检查 BuildGate 结果
        build_result = context.get("build_gate")
        if build_result and not build_result.success:
            self.log.warning("[build_gate] Build failed after Builder — skipping commit and evaluation")
            self.dashboard.add_alert(f"Build failed in round {round_num}, skipping eval")
            self.git.rollback_to(
                self.git.get_head_hash(),
                f"Build failed in round {round_num} — rolling back uncommitted changes"
            )
            return self._handle_pipeline_failure(
                round_num, round_start, "build_gate",
                build_result.message, 0.0,
                build_usage=build_usage, strategy=strategy
            )

        # 检查 DevServerGate 结果
        dev_server_result = context.get("dev_server_gate")
        if dev_server_result and not dev_server_result.success:
            self.log.warning(f"[dev_server_gate] {dev_server_result.message}")
            self.dashboard.add_alert(f"Round {round_num}: Dev server failed")
            return self._handle_pipeline_failure(
                round_num, round_start, "dev_server_gate",
                dev_server_result.message, 0.0,
                build_usage=build_usage, strategy=strategy
            )

        # ScreenshotGate 和 GitCommit 已在 Pipeline 中完成（不阻塞）

        # ===== Evaluate =====
        return self._run_evaluation(round_num, round_start, build_usage, strategy)

    def _prepare_rollback_msg(self) -> str:
        """准备 Builder 的提示消息（功能组推进版）。"""
        msg = ""

        # Feature-group stuck detection
        if self.feature_groups:
            stuck, stuck_gid = self.feature_groups.any_group_stuck()
            if stuck:
                msg = (
                    f"\nNOTE: 功能组 {stuck_gid} 已连续 {self.feature_groups.stuck_counts.get(stuck_gid, 0)} "
                    f"轮未通过。请尝试完全不同的实现策略，或简化该功能组的核心需求。"
                )
            else:
                cg = self.feature_groups.current_group
                if cg:
                    rate = self.feature_groups.pass_rates.get(cg["id"], 0.0)
                    if rate > 0 and rate < 1.0:
                        msg = (
                            f"\nNOTE: 功能组 {cg['id']} 上轮通过率 {rate:.0%}，尚未达标。"
                            f"继续修复该功能组的未通过项。"
                        )

        # Legacy fallback: contract rate drop (only when no feature-group state)
        elif self.contract_pass_rate_history and len(self.contract_pass_rate_history) >= 2:
            best_idx = max(range(len(self.contract_pass_rate_history)), key=lambda i: self.contract_pass_rate_history[i])
            best_contract = self.contract_pass_rate_history[best_idx]
            last_contract = self.contract_pass_rate_history[-1]
            SIGNIFICANT_DROP_RATE = 0.10
            if last_contract < best_contract - SIGNIFICANT_DROP_RATE:
                best_hash = self.git.get_commit_for_round(best_idx + 1)
                if best_hash:
                    self.git.rollback_to(
                        best_hash,
                        f"Contract rate dropped {last_contract:.0%} — best was {best_contract:.0%} at round {best_idx + 1}"
                    )
                    msg = (
                        f"\nNOTE: Contract pass rate dropped to {last_contract:.0%}. "
                        f"Rolled back to round {best_idx + 1} (best contract {best_contract:.0%}). "
                        f"The last approach broke existing functionality. Fix or change strategy."
                    )
        return msg

    def _needs_env_check(self) -> bool:
        """判断是否需要运行 PreBuildGate。"""
        pkg = self.workspace / "package.json"
        nm = self.workspace / "node_modules"
        return not pkg.exists() or not nm.exists()

    def _handle_pipeline_failure(
        self, round_num: int, round_start: float,
        failed_stage: str, message: str, penalty_rate: float,
        build_usage: dict | None = None, strategy: dict | None = None
    ) -> dict:
        """统一处理 Pipeline 阶段失败的逻辑。返回 dict 供双轨评分使用。"""
        sprint_rate = penalty_rate
        contract_rate = penalty_rate
        self.sprint_pass_rate_history.append(sprint_rate)
        self.contract_pass_rate_history.append(contract_rate)
        # Back-compat: keep old lists populated with pass rates
        self.sprint_score_history.append(sprint_rate)
        self.overall_score_history.append(contract_rate)
        self.score_history.append(contract_rate)

        round_prompt = build_usage["prompt"] if build_usage else 0
        round_completion = build_usage["completion"] if build_usage else 0
        self.token_totals["prompt"] += round_prompt
        self.token_totals["completion"] += round_completion

        elapsed = time.time() - round_start
        self.round_stats.append({
            "round": round_num,
            "score": contract_rate,
            "strategy": strategy["strategy"] if strategy else "UNKNOWN",
            "prompt_tokens": round_prompt,
            "completion_tokens": round_completion,
            "elapsed_s": elapsed,
        })
        log_round_stats(self.log, round_num, contract_rate, sprint_rate, contract_rate,
                       round_prompt, round_completion, elapsed)
        self.dashboard.update_scores(sprint_rate, contract_rate)
        self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])
        return {"sprint_rate": sprint_rate, "contract_rate": contract_rate, "score": contract_rate}

    def _run_evaluation(self, round_num: int, round_start: float, build_usage: dict, strategy: dict) -> dict:
        """运行 Reviewer + Judge 评估阶段（双轨评分版）。"""
        contract_ref = config.CONTRACT_FILE

        # Step 1: Reviewer
        self.log.info("Evaluate phase — Step 1: Reviewer (unified review)")
        self.dashboard.start_agent("Reviewer")

        # Inject current feature group into Reviewer task
        group_section = ""
        if self.feature_groups and self.feature_groups.current_group:
            cg = self.feature_groups.current_group
            group_section = (
                f"\n当前验证功能组: {cg['id']} — {cg['name']}\n"
                f"验收标准范围: {cg['id']}.1 ~ {cg['id']}.{len(cg['criteria'])}\n"
                f"你只验证这个功能组的标准，不要检查其他功能组。\n"
            )

        review_task = (
            f"Review the codebase and test the web app for round {round_num}.\n"
            f"{group_section}"
            f"Read the acceptance criteria in {contract_ref}, then:\n"
            f"1. Examine the MOST IMPORTANT source files (max 5 files) related to the current feature group.\n"
            f"2. Run browser tests (desktop + mobile). Dev server is already running.\n"
            f"3. Produce a unified review report focused ONLY on the current feature group.\n"
            f"Limit: 15 iterations max."
        )
        review_result, review_usage = self.reviewer.run_with_stats(review_task)

        # FIX: Detect incomplete Reviewer reports and protect against Judge over-scoring.
        # If the Reviewer hit max iterations, its report may be partial. We mark it clearly
        # and cap the contract rate to prevent false-high scores.
        reviewer_status = "success"
        if "[REVIEWER STATUS: INCOMPLETE" in review_result:
            self.log.warning(
                f"[reviewer] Round {round_num} review is INCOMPLETE (max iterations). "
                f"Capping contract rate to prevent over-scoring."
            )
            reviewer_status = "incomplete"
            # Prepend a clear header so Judge cannot miss the incomplete status
            review_result = (
                f"# ⚠️ REVIEWER REPORT — STATUS: INCOMPLETE (Round {round_num})\n\n"
                f"**The Reviewer hit the iteration limit before completing all tests.**\n\n"
                f"**GUIDANCE FOR JUDGE:**\n"
                f"1. The Reviewer's browser tests may be partial or unavailable.\n"
                f"2. You MUST read the source code yourself to perform code review.\n"
                f"3. For conditionally-rendered features (e.g., controls that appear after upload),\n"
                f"   the Reviewer may not have been able to trigger the condition.\n"
                f"4. Base your scoring on: CODE EXISTENCE > browser absence for conditionally-rendered features.\n"
                f"5. Only mark FAIL if the code itself is missing, stubbed, or obviously broken.\n"
                f"6. Do NOT mark FAIL solely because an element is hidden in the initial DOM state.\n\n"
                f"---\n\n"
                f"{review_result}"
            )
        elif review_result.startswith("[error]"):
            self.log.error(f"[reviewer] Round {round_num} review failed: {review_result[:200]}")
            reviewer_status = "error"
            # Construct a minimal failure report so Judge has something to read
            review_result = (
                f"# REVIEWER REPORT — STATUS: FAILED (Round {round_num})\n\n"
                f"**The Reviewer encountered an error and produced no test results.**\n\n"
                f"**RULES FOR JUDGE:**\n"
                f"1. No browser tests were performed.\n"
                f"2. All criteria MUST be marked FAIL.\n"
                f"3. CONTRACT_PASS_RATE = 0%.\n\n"
                f"Original error: {review_result}\n"
            )

        self.eval_cache.save_round(round_num, review_result)
        self.dashboard.end_agent(reviewer_status)

        # Step 2: Judge
        self.log.info("Evaluate phase — Step 2: Judge (pass-rate scoring)")
        self.dashboard.start_agent("Judge")
        # Judge reads the full Reviewer report directly from disk
        review_report_path = self.workspace / ".eval_cache" / f"round_{round_num}_review.md"
        review_hint = ""
        if review_report_path.exists():
            review_hint = (
                f"\n\nIMPORTANT: Read the complete Reviewer report at "
                f"`.eval_cache/round_{round_num}_review.md` before scoring. "
                f"The Reviewer's browser test results are the highest-priority evidence.\n\n"
            )
        judge_task = (
            f"Round {round_num} evaluation.\n\n"
            f"Read {config.SPRINT_FILE}, {config.CONTRACT_FILE}, "
            f"and any previous {config.FEEDBACK_FILE}, then produce feedback.md with pass rates."
            f"{review_hint}"
        )
        judge_result, judge_usage = self.judge.run_with_stats(judge_task)
        self.dashboard.end_agent("success")

        # Parse pass rates
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = judge_result
        else:
            eval_text = judge_result

        # ------------------------------------------------------------------ #
        #  Feature-group driven scoring (NEW)
        # ------------------------------------------------------------------ #
        group_rate, overall_rate = parse_group_pass_rates(eval_text)

        # Fallback to legacy parsing
        sprint_rate, contract_rate = parse_pass_rates(eval_text)
        if group_rate is not None:
            contract_rate = group_rate
        if overall_rate is not None:
            # Use overall_rate as the contract rate for back-compat
            pass
        elif contract_rate is None:
            contract_rate = 0.0
        if sprint_rate is None:
            sprint_rate = 0.0

        # ------------------------------------------------------------------ #
        #  Cross-validate with contract.md true denominator
        # ------------------------------------------------------------------ #
        contract_path = self.workspace / config.CONTRACT_FILE
        if contract_path.exists():
            contract_text = contract_path.read_text(encoding="utf-8", errors="replace")
            review_text = self.eval_cache.get_full_report(round_num) or ""
            # In feature-group mode, only validate against current group's criteria
            group_id_for_validation = None
            if self.feature_groups and self.feature_groups.current_group:
                group_id_for_validation = self.feature_groups.current_group_id
            true_rate, true_passed, true_failed, true_skipped, total_contract, overrides = (
                compute_actual_contract_rate(
                    eval_text, contract_text, review_text,
                    current_group_id=group_id_for_validation,
                )
            )
            if total_contract > 0:
                judge_reported_total = true_passed + true_failed + true_skipped
                if abs(contract_rate - true_rate) > 0.05 or judge_reported_total < total_contract:
                    self.log.warning(
                        f"[judge] Judge reported {contract_rate:.0%} ({judge_reported_total} criteria), "
                        f"but true rate over {total_contract} criteria is {true_rate:.0%}. "
                        f"Using true rate."
                    )
                    contract_rate = true_rate
                if overrides:
                    for ov in overrides:
                        self.log.warning(f"[judge] {ov}")
        else:
            self.log.warning("[judge] contract.md not found, cannot verify true denominator")

        # Detect excessive SKIP ratio
        skip_rate = parse_skip_rate(eval_text)
        if skip_rate > 0.20 and contract_rate >= config.CONTRACT_PASS_RATE_THRESHOLD:
            adjusted_contract = contract_rate * (1 - skip_rate)
            self.log.warning(
                f"[judge] Judge gave {contract_rate:.0%} contract but SKIP ratio is {skip_rate:.0%}. "
                f"Adjusting contract rate to {adjusted_contract:.0%} to prevent premature termination."
            )
            contract_rate = adjusted_contract

        # Cap scores when Reviewer report is incomplete or failed
        # NOTE: For conditionally-rendered features, INCOMPLETE often means the Reviewer
        # couldn't trigger the condition (e.g., upload a file). We raise the cap from 30%
        # to 50% to give Judge room to score based on code review, while still preventing
        # false-high scores from incomplete testing.
        if reviewer_status == "incomplete":
            capped_contract = min(contract_rate, 0.50)
            capped_sprint = min(sprint_rate, 0.50)
            if capped_contract < contract_rate or capped_sprint < sprint_rate:
                self.log.warning(
                    f"[judge] Judge gave {contract_rate:.0%} contract / {sprint_rate:.0%} sprint, "
                    f"but Reviewer report was INCOMPLETE. Capping to "
                    f"{capped_contract:.0%} / {capped_sprint:.0%}."
                )
            contract_rate = capped_contract
            sprint_rate = capped_sprint
        elif reviewer_status == "error":
            if contract_rate > 0 or sprint_rate > 0:
                self.log.warning(
                    f"[judge] Judge gave {contract_rate:.0%} contract / {sprint_rate:.0%} sprint, "
                    f"but Reviewer FAILED. Forcing to 0%."
                )
            contract_rate = 0.0
            sprint_rate = 0.0

        # ------------------------------------------------------------------ #
        #  Update feature-group state (NEW)
        # ------------------------------------------------------------------ #
        if self.feature_groups and self.feature_groups.current_group:
            gid = self.feature_groups.current_group_id
            self.feature_groups.update_rate(gid, contract_rate)
            # Advance to next group if current group passed
            if self.feature_groups.check_should_advance():
                advanced = self.feature_groups.advance()
                if advanced:
                    self.log.info(
                        f"[feature_groups] {gid} passed — advancing to "
                        f"{self.feature_groups.current_group_id}"
                    )
            # Log tier status
            ts = self.feature_groups.tier_status()
            for tier, st in ts.items():
                status = "✅" if st["passed"] else "⏳"
                self.log.info(
                    f"[tier] {tier}: {st['groups_complete']}/{st['groups_total']} "
                    f"groups ({st['rate']:.0%}) {status}"
                )
            self.log.info(
                f"[overall] {self.feature_groups.overall_rate():.0%} "
                f"({self.feature_groups.current_group_id} at {contract_rate:.0%})"
            )

        self.sprint_pass_rate_history.append(sprint_rate)
        self.contract_pass_rate_history.append(contract_rate)
        # Back-compat
        self.sprint_score_history.append(sprint_rate)
        self.overall_score_history.append(contract_rate)
        self.score_history.append(contract_rate)
        self.log.info(f"  Group pass rate: {contract_rate:.0%} | Overall: {self.feature_groups.overall_rate():.0%}" if self.feature_groups else f"  Sprint pass rate: {sprint_rate:.0%} | Contract pass rate: {contract_rate:.0%}")

        round_prompt = build_usage["prompt"] + review_usage["prompt"] + judge_usage["prompt"]
        round_completion = build_usage["completion"] + review_usage["completion"] + judge_usage["completion"]
        self.token_totals["prompt"] += round_prompt
        self.token_totals["completion"] += round_completion
        elapsed = time.time() - round_start
        self.round_stats.append({
            "round": round_num,
            "score": contract_rate,
            "strategy": strategy["strategy"],
            "prompt_tokens": round_prompt,
            "completion_tokens": round_completion,
            "elapsed_s": elapsed,
        })
        log_round_stats(self.log, round_num, contract_rate, sprint_rate, contract_rate,
                       round_prompt, round_completion, elapsed)
        self.dashboard.update_scores(sprint_rate, contract_rate)
        self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])

        self.log.info(
            f"[round_budget] Round {round_num} complete | "
            f"elapsed: {elapsed:.0f}s | "
            f"tokens: {round_prompt}p+{round_completion}c | "
            f"total: {self.token_totals['prompt']}p+{self.token_totals['completion']}c"
        )

        # Browser resources are managed per-call (independent bridge pattern)
        # No global cleanup needed

        return {"sprint_rate": sprint_rate, "contract_rate": contract_rate, "score": contract_rate}

    def _build_build_task(self, round_num: int, rollback_msg: str) -> str:
        """Build the task prompt for the Builder agent."""
        sprint_path = self.workspace / config.SPRINT_FILE
        contract_path = self.workspace / config.CONTRACT_FILE
        feedback_path = self.workspace / config.FEEDBACK_FILE

        # Feature-group injection (NEW)
        group_section = ""
        if self.feature_groups and self.feature_groups.current_group:
            cg = self.feature_groups.current_group
            group_section = get_group_instruction(cg["id"], cg) + "\n\n"

        # Primary guide: sprint.md (自带验收标准)
        if sprint_path.exists():
            primary_guide = f"1. Read {config.SPRINT_FILE} — this is your task list for this round.\n"
        else:
            primary_guide = f"1. Read {config.SPEC_FILE} for product spec.\n"

        # Global contract for current group only
        if contract_path.exists():
            primary_guide += f"2. Read {config.CONTRACT_FILE} — 只关注当前功能组 {self.feature_groups.current_group_id if self.feature_groups else 'N/A'} 的标准。\n"

        task = f"Round {round_num} of building.{rollback_msg}\n\n{group_section}Steps:\n{primary_guide}"

        if feedback_path.exists() and round_num > 1:
            task += f"3. Read {config.FEEDBACK_FILE} for previous feedback on the current feature group.\n"

        # Inject feature-group progress hint
        if self.feature_groups:
            ts = self.feature_groups.tier_status()
            tier_hint = "\n".join(
                f"  {cfg['label']}: {ts[tier]['groups_complete']}/{ts[tier]['groups_total']} 组完成"
                for tier, cfg in TIER_REQUIREMENTS.items() if tier in ts
            )
            task += f"\n功能组进度:\n{tier_hint}\n"
            task += f"总体进度: {self.feature_groups.overall_rate():.0%}\n"

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
        """将完整历史分数压缩为趋势摘要（双轨评分版）"""
        if not self.contract_pass_rate_history:
            return "No scores yet."
        recent = self.contract_pass_rate_history[-5:]
        trend = " -> ".join(f"{s:.0%}" for s in recent)
        best_idx = max(range(len(self.contract_pass_rate_history)), key=lambda i: self.contract_pass_rate_history[i])
        best_round = best_idx + 1
        best_score = self.contract_pass_rate_history[best_idx]
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
        hard_limit = int(conservative * 1.2)
        budget_msg = f"""
## Iteration Budget（严格限制）
本轮保守预算：{conservative} 次迭代。
硬上限：{hard_limit} 次迭代（达到后强制停止）。

预算使用指南：
- 0-{int(conservative*0.4)} 次：正常编码阶段
- {int(conservative*0.4)+1}-{threshold} 次：收尾阶段，停止新功能
- {threshold+1}-{hard_limit} 次：仅修复阻塞性 bug
- >{hard_limit} 次：强制停止，声明 REFINE 策略

如果连续 5 次迭代都在修复同一个环境问题（如 TypeScript、npm、路径等），
立即停止并声明 PIVOT 策略，请求重新初始化项目。
"""
        return build_task + budget_msg

    # ------------------------------------------------------------------ #
    #      Dynamic Sprint Task Limit                                    #
    # ------------------------------------------------------------------ #
    def _get_last_builder_iterations(self) -> list[int]:
        """Read Builder's actual iteration counts from .events/Builder.jsonl.

        Returns a list of iteration counts for each completed round.
        """
        events_path = self.workspace / ".events" / "Builder.jsonl"
        if not events_path.exists():
            return []

        iterations: list[int] = []
        try:
            with events_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("agent") == "Builder" and "iterations" in data:
                            iterations.append(int(data["iterations"]))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception as e:
            self.log.debug(f"[sprint_limit] Could not read Builder events: {e}")

        return iterations

    def _calculate_sprint_task_limit(self) -> int:
        """基于 Builder 历史表现动态计算本轮任务数量上限。

        逻辑：
        - 默认 2 个任务
        - 如果 Builder 最近一轮提前完成（实际迭代 < 保守预算 × 0.7），增加到 3 个
        - 如果上一轮超时、失败或策略为 PIVOT，减到 1 个
        - Round 1 固定 1 个任务（项目初始化 + 1 个核心功能）
        """
        round_num = self._completed_rounds + 1
        if round_num == 1:
            return 1

        builder_iters = self._get_last_builder_iterations()
        if not builder_iters:
            return 2

        last_iter = builder_iters[-1]

        # 检查上轮策略
        last_strategy = "UNKNOWN"
        if self.strategy_history:
            last_strategy = self.strategy_history[-1].get("strategy", "UNKNOWN")

        # 检查上轮是否失败（contract rate 为 0 或 PIVOT）
        if last_strategy == "PIVOT":
            self.log.info(f"[sprint_limit] Last round was PIVOT -> limit=1")
            return 1

        if self.contract_pass_rate_history:
            last_contract = self.contract_pass_rate_history[-1]
            if last_contract == 0.0:
                self.log.info(f"[sprint_limit] Last contract rate was 0% -> limit=1")
                return 1

        # 从 sprint.md 读取上轮保守预算
        sprint_path = self.workspace / config.SPRINT_FILE
        conservative_budget = 25
        if sprint_path.exists():
            try:
                sprint_text = sprint_path.read_text(encoding="utf-8", errors="replace")
                import re
                match = re.search(r'[保守|Conservative|conservative][：:]\s*(\d+)', sprint_text)
                if match:
                    conservative_budget = int(match.group(1))
            except Exception:
                pass

        # 提前完成判定：实际迭代 < 保守预算 × 0.7
        efficiency = last_iter / conservative_budget
        self.log.info(
            f"[sprint_limit] Last build: {last_iter} iter / {conservative_budget} budget "
            f"(efficiency={efficiency:.1%})"
        )

        if efficiency < 0.7:
            # 表现优秀，可以增加到 3 个任务
            self.log.info("[sprint_limit] Builder under budget -> limit=3")
            return 3
        elif efficiency > 1.0:
            # 超时，减到 1 个任务
            self.log.info("[sprint_limit] Builder over budget -> limit=1")
            return 1

        # 正常完成，保持 2 个任务
        return 2

    def _get_builder_budget_hint(self) -> str:
        """生成 Builder 历史表现的提示信息，供 SprintMaster 参考。"""
        builder_iters = self._get_last_builder_iterations()
        if not builder_iters:
            return ""

        recent = builder_iters[-3:]
        avg_iter = sum(recent) / len(recent)
        hint = f"Builder 最近 {len(recent)} 轮平均迭代数：{avg_iter:.0f} 次。"

        # 如果最近一轮特别快，提示可以安排更多工作
        if len(builder_iters) >= 2:
            if builder_iters[-1] < builder_iters[-2] * 0.8:
                hint += " 最近一轮效率明显提升，可以适当增加任务量。"
            elif builder_iters[-1] > builder_iters[-2] * 1.3:
                hint += " 最近一轮耗时增加，建议控制任务范围。"

        return hint

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
        """从 spec.md 解析功能点，估算基础轮数
        
        修复：更准确地估算功能点数量，考虑 Phase 分层
        """
        spec_path = self.workspace / config.SPEC_FILE
        if not spec_path.exists():
            return 5

        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        
        # 更精确的功能计数：统计 F1, F2, ... 格式的功能编号
        import re
        feature_matches = re.findall(r'\*\*F\d+[:：]', spec_text)
        feature_count = len(feature_matches)
        
        # 统计 Phase 数量
        phase_matches = re.findall(r'### Phase \d+', spec_text)
        phase_count = len(phase_matches)
        
        # 统计图片资源需求
        asset_count = len(re.findall(r'generate_image|hero-gradient|theme-.*-preview|empty-state', spec_text))
        
        # 基础轮数：每 Phase 至少 1 轮骨架 + 每个功能约 1 轮
        rounds = phase_count  # 每个 Phase 至少 1 轮
        rounds += feature_count  # 每个功能约 1 轮
        rounds += asset_count // 3   # 图片生成每 3 张约 1 轮
        
        # 保底：至少 4 轮（避免复杂项目被严重低估），但不超过硬上限
        return max(min(rounds, 10), 4)

    def _runtime_adjustment(self) -> int:
        """基于已跑轮次表现，追加或缩减（双轨评分版）"""
        if not self.sprint_pass_rate_history:
            return 0

        # 信号 A：连续高 Sprint 通过率但 Contract 没过 -> 功能多，需要更多轮
        recent = self.sprint_pass_rate_history[-2:]
        if len(recent) == 2 and all(s >= config.SPRINT_PASS_RATE_THRESHOLD for s in recent):
            if not self.contract_pass_rate_history or self.contract_pass_rate_history[-1] < config.CONTRACT_PASS_RATE_THRESHOLD:
                return 2

        # 信号 B：连续停滞（Contract 几乎不变）-> 缩减，逼迫 PIVOT
        if len(self.contract_pass_rate_history) >= 3:
            last_three = self.contract_pass_rate_history[-3:]
            if max(last_three) - min(last_three) < 0.05:  # 5 percentage points
                return -1

        # 信号 C：Contract 持续上升且已接近门槛 -> 追加 1 轮冲刺
        if self.contract_pass_rate_history and self.contract_pass_rate_history[-1] >= 0.60:
            return 1

        return 0

    # ------------------------------------------------------------------ #
    #      Feature-group exit condition                                   #
    # ------------------------------------------------------------------ #
    def _check_exit_condition(self) -> tuple[bool, str]:
        """检查是否满足退出条件（功能组推进版）。

        成功条件：
        1. Tier 1（F1-F4）所有组 100% 通过
        2. Tier 2（F5-F9）所有组 ≥ 80% 通过
        3. Overall ≥ 75%

        失败/继续条件：
        - 任一条件不满足 → 继续
        """
        if not self.feature_groups:
            # Legacy mode: use contract rate threshold
            if self.contract_pass_rate_history:
                rate = self.contract_pass_rate_history[-1]
                if rate >= config.CONTRACT_PASS_RATE_THRESHOLD:
                    return True, f"Contract pass rate {rate:.0%} >= threshold"
                return False, f"Contract pass rate {rate:.0%} below threshold"
            return False, "No scores yet"

        ts = self.feature_groups.tier_status()
        overall = self.feature_groups.overall_rate()

        # Check Tier 1
        if not ts["tier1"]["passed"]:
            return False, (
                f"Tier 1 MVP incomplete: {ts['tier1']['groups_complete']}/"
                f"{ts['tier1']['groups_total']} groups, overall {overall:.0%}"
            )

        # Check Tier 2
        if not ts["tier2"]["passed"]:
            return False, (
                f"Tier 2 Core incomplete: {ts['tier2']['groups_complete']}/"
                f"{ts['tier2']['groups_total']} groups, overall {overall:.0%}"
            )

        # Check overall
        if overall < OVERALL_PASS_THRESHOLD:
            return False, (
                f"Overall {overall:.0%} below threshold {OVERALL_PASS_THRESHOLD:.0%}"
            )

        # Check stuck groups
        stuck, stuck_gid = self.feature_groups.any_group_stuck()
        if stuck:
            return False, f"Group {stuck_gid} stuck for {self.feature_groups.stuck_counts.get(stuck_gid, 0)} rounds"

        return True, (
            f"Tier 1 ✅, Tier 2 ✅, Overall {overall:.0%} ✅"
        )

    def _strategy_adjustment(self) -> int:
        """Builder 策略信号（双轨评分版）"""
        if not self.strategy_history:
            return 0

        recent_strategies = [s["strategy"] for s in self.strategy_history[-2:]]

        # 连续 PIVOT：架构在重构，给额外轮次
        if recent_strategies == ["PIVOT", "PIVOT"]:
            return 2

        # 连续 REFINE 且 Contract 率上升：势头好，给 1 轮奖励
        if recent_strategies == ["REFINE", "REFINE"]:
            if len(self.contract_pass_rate_history) >= 2 and \
               self.contract_pass_rate_history[-1] > self.contract_pass_rate_history[-2]:
                return 1

        return 0
