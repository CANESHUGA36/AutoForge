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
from harness.eval import parse_pass_rates
from harness.git import GitManager
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

            # 双轨评分：Contract 通过率 >= 75% 才允许跳出
            if contract_rate >= config.CONTRACT_PASS_RATE_THRESHOLD:
                self.log.info(f"\nSuccess! Contract pass rate: {contract_rate:.0%}")
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
                    f"Contract pass rate {contract_rate:.0%} below threshold "
                    f"{config.CONTRACT_PASS_RATE_THRESHOLD:.0%}, continuing..."
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
        rollback_msg = self._prepare_rollback_msg()

        # Sprint
        sprint_ok = plan_sprint_master(self.workspace, round_num, self.sprint_master, self.log)
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
        """准备 Builder 的 rollback 提示消息（双轨评分版）。"""
        rollback_msg = ""
        if self.sprint_pass_rate_history and self.sprint_pass_rate_history[-1] < config.SPRINT_PASS_RATE_THRESHOLD:
            latest_hash = self.git.get_head_hash()
            rate = self.sprint_pass_rate_history[-1]
            if latest_hash:
                self.git.rollback_to(
                    latest_hash,
                    f"Sprint pass rate {rate:.0%} below threshold {config.SPRINT_PASS_RATE_THRESHOLD:.0%}"
                )
                rollback_msg = (
                    f"\nNOTE: Last sprint pass rate {rate:.0%} "
                    f"(below threshold {config.SPRINT_PASS_RATE_THRESHOLD:.0%}). "
                    f"You MUST fix the failing criteria from the last sprint BEFORE adding new features. "
                    f"Do NOT move on to new tasks until this sprint passes."
                )
            else:
                rollback_msg = (
                    f"\nNOTE: Last sprint pass rate {rate:.0%} "
                    f"(below threshold {config.SPRINT_PASS_RATE_THRESHOLD:.0%}). "
                    f"Fix the current implementation before proceeding."
                )
        elif self.contract_pass_rate_history and len(self.contract_pass_rate_history) >= 2:
            best_idx = max(range(len(self.contract_pass_rate_history)), key=lambda i: self.contract_pass_rate_history[i])
            best_contract = self.contract_pass_rate_history[best_idx]
            last_contract = self.contract_pass_rate_history[-1]
            SIGNIFICANT_DROP_RATE = 0.10  # 10 percentage points
            if last_contract < best_contract - SIGNIFICANT_DROP_RATE:
                best_hash = self.git.get_commit_for_round(best_idx + 1)
                if best_hash:
                    self.git.rollback_to(
                        best_hash,
                        f"Contract rate dropped {last_contract:.0%} — best was {best_contract:.0%} at round {best_idx + 1}"
                    )
                    rollback_msg = (
                        f"\nNOTE: Contract pass rate dropped to {last_contract:.0%}. "
                        f"Rolled back to round {best_idx + 1} (best contract {best_contract:.0%}). "
                        f"The last approach broke existing functionality. Fix or change strategy."
                    )
        return rollback_msg

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
        review_task = (
            f"Review the codebase and test the web app for round {round_num}.\n"
            f"Read the acceptance criteria in {contract_ref}, then:\n"
            f"1. Examine the MOST IMPORTANT source files (max 8 files).\n"
            f"2. Run browser tests (desktop + mobile). Dev server is already running.\n"
            f"3. Produce a unified review report.\n"
            f"Limit: 15 iterations max."
        )
        review_result, review_usage = self.reviewer.run_with_stats(review_task)
        self.eval_cache.save_round(round_num, review_result)
        self.dashboard.end_agent("success")

        # Step 2: Judge
        self.log.info("Evaluate phase — Step 2: Judge (pass-rate scoring)")
        self.dashboard.start_agent("Judge")
        review_report = self.eval_cache.get_full_report(round_num) or ""
        review_hint = ""
        if review_report:
            review_hint = f"\n\nReviewer report for this round:\n{review_report[:4000]}\n\n"
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

        sprint_rate, contract_rate = parse_pass_rates(eval_text)
        if sprint_rate is None or contract_rate is None:
            log.warning("Could not parse pass rates from judge feedback, defaulting to 0")
            sprint_rate = sprint_rate or 0.0
            contract_rate = contract_rate or 0.0

        self.sprint_pass_rate_history.append(sprint_rate)
        self.contract_pass_rate_history.append(contract_rate)
        # Back-compat
        self.sprint_score_history.append(sprint_rate)
        self.overall_score_history.append(contract_rate)
        self.score_history.append(contract_rate)
        self.log.info(f"  Sprint pass rate: {sprint_rate:.0%} | Contract pass rate: {contract_rate:.0%}")

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

            if len(self.contract_pass_rate_history) >= 2:
                delta = self.contract_pass_rate_history[-1] - self.contract_pass_rate_history[-2]
                if delta > 0:
                    task += f"\nTrend: Improving (+{delta:.0%}), continue refining."
                elif delta < 0:
                    task += f"\nTrend: Declining ({delta:.0%}), consider pivoting."
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
