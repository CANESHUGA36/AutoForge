#!/usr/bin/env python3
"""
Harness        ?
    Anthropic     "Harness design for long-running application development"
"""
from __future__ import annotations
import json
import logging
import re
import time
from pathlib import Path
import config
from agents import Agent
from dashboard import Dashboard
from eval_cache import EvalCache
from prompts import (
    ARCHITECT_SYSTEM, BUILDER_SYSTEM, REVIEWER_SYSTEM, SPRINT_MASTER_SYSTEM,
    refresh_prompts,
)
from tools_impl import TOOL_SCHEMAS, BROWSER_TOOL_SCHEMAS
from harness.eval import parse_pass_rates, parse_skip_rate, compute_actual_contract_rate, parse_group_pass_rates
from harness.git import GitManager
from harness.framework_adapter import get_framework_adapter
from harness.contract_tests import ContractTestRunner
from harness.react_devtools import ReactDevToolsChecker
from harness.feature_groups import (
    FeatureGroupState, parse_feature_groups, get_group_instruction,
    OVERALL_PASS_THRESHOLD, GROUP_PASS_THRESHOLD_DEFAULT,
    _check_exit_condition_dynamic,
)
from harness.logging import setup_file_logging, log_round_stats, log_final_stats
from harness.sprint import plan_sprint_master
from harness.state import StateManager
from harness.shared_state import SharedState, load_shared_state, save_shared_state
from harness.strategy import parse_strategy
from harness.events import EventBus
from harness.pipeline import PipelineRunner
from harness.stages import (
    PreBuildGateStage, BuildGateStage, DesignLintStage, DevServerGateStage,
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
        # 刷新 prompts 缓存，使 {{WORKSPACE}} 等模板变量使用最新路径
        refresh_prompts()
        #                 Logger
        self.log = logging.getLogger(f"harness.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        #          
        self.git = GitManager(self.workspace)
        self.state_mgr = StateManager(self.workspace)
        self.git.init_repo()
        setup_file_logging(self.workspace, self.log)
        # 加载共享状态
        self.shared_state = load_shared_state(str(self.workspace))
        #     Agent（按职能细分工具集）
        CORE_TOOLS = {"read_file", "write_file", "read_skill_file"}
        FILE_TOOLS = {"edit_file", "list_files"}
        EXEC_TOOLS = {"run_bash"}
        BROWSER_TOOLS = {"browser_check"}
        GEN_TOOLS = {"generate_image", "search_web", "analyze_image"}
        META_TOOLS = {"validate_build", "project_init"}

        architect_tools = CORE_TOOLS | GEN_TOOLS | {"search_web"}
        sprint_master_tools = CORE_TOOLS | FILE_TOOLS | {"list_files"}
        builder_tools = CORE_TOOLS | FILE_TOOLS | EXEC_TOOLS | GEN_TOOLS | META_TOOLS
        reviewer_tools = CORE_TOOLS | FILE_TOOLS | BROWSER_TOOLS | {"contract_test_run", "react_devtools_inspect", "check_console_logs", "detect_framework", "run_diagnostics", "check_responsive", "check_a11y", "check_performance", "check_routes", "mock_api"}

        # 注入共享状态到 system prompts
        self._inject_shared_state_into_prompts()
        
        self.architect = Agent("Architect", self.architect_prompt, TOOL_SCHEMAS, allowed_tools=architect_tools, logger=self.log)
        self.sprint_master = Agent("SprintMaster", self.sprint_master_prompt, TOOL_SCHEMAS, use_state=True, allowed_tools=sprint_master_tools, logger=self.log)
        self.builder = Agent("Builder", self.builder_prompt, TOOL_SCHEMAS, use_state=True, allowed_tools=builder_tools, logger=self.log)
        self.reviewer = Agent("Reviewer", self.reviewer_prompt, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, use_state=True, allowed_tools=reviewer_tools, logger=self.log)
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
    
    def _inject_shared_state_into_prompts(self) -> None:
        """将共享状态注入到各 Agent 的 system prompt 中
        
        注入位置：放在 system prompt 末尾，用显式分隔线包裹，
        确保 Agent 在每次对话开始时都能看到。
        """
        current_round = self._completed_rounds if hasattr(self, '_completed_rounds') else 0
        
        # Convert _PromptProxy to str first
        self.architect_prompt = str(ARCHITECT_SYSTEM)
        self.sprint_master_prompt = str(SPRINT_MASTER_SYSTEM)
        self.builder_prompt = str(BUILDER_SYSTEM)
        self.reviewer_prompt = str(REVIEWER_SYSTEM)
        
        # 如果有共享状态，注入到 prompts
        if hasattr(self, 'shared_state') and self.shared_state:
            # Builder: 需要架构决策、已知陷阱、已验证模式
            shared_section = self.shared_state.to_prompt_section("Builder", current_round)
            if shared_section:
                self.builder_prompt += f"\n\n{shared_section}"
                self.log.info(f"[shared_state] Injected {len(shared_section)} chars into Builder prompt")
            
            # Reviewer: 需要验证捷径、项目类型
            shared_section = self.shared_state.to_prompt_section("Reviewer", current_round)
            if shared_section:
                self.reviewer_prompt += f"\n\n{shared_section}"
                self.log.info(f"[shared_state] Injected {len(shared_section)} chars into Reviewer prompt")
            
            # SprintMaster: 需要架构决策、当前组信息
            shared_section = self.shared_state.to_prompt_section("SprintMaster", current_round)
            if shared_section:
                self.sprint_master_prompt += f"\n\n{shared_section}"
                self.log.info(f"[shared_state] Injected {len(shared_section)} chars into SprintMaster prompt")
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
            # FIX: Save feature_groups state so resume works correctly
            "feature_groups_state": self.feature_groups.to_dict() if self.feature_groups else None,
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
        # FIX: Restore feature_groups state will be done after contract is parsed in _build_round
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
            
            # Extract tech stack from spec for shared state
            try:
                spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
                self._extract_tech_stack_from_spec(spec_text)
            except Exception as e:
                self.log.debug(f"[shared_state] Failed to extract tech stack: {e}")
        
        # Phase 2+: Build-Evaluate loop
        self.dashboard.start_run()
        start_round = self._completed_rounds + 1
        hard_limit = getattr(config, 'MAX_ROUNDS_HARD', 15)
        round_num = start_round
        
        while round_num <= hard_limit:
            # FIX BUG #9: Recalculate max_rounds each round so runtime adjustments apply
            # Respect explicit MAX_HARNESS_ROUNDS env var if set (> 0)
            explicit_max = getattr(config, 'MAX_ROUNDS', 0)
            if explicit_max > 0:
                max_rounds = explicit_max
            else:
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
            round_num += 1

            sprint_rate = result.get("sprint_rate", 0.0)
            contract_rate = result.get("contract_rate", 0.0)
            score = result.get("score", 0.0)

            # Feature-group based exit condition
            exit_ok, exit_reason = self._check_exit_condition()
            if exit_ok:
                self.log.info(f"\nSuccess! {exit_reason}")
                
                # ===== FINAL REVIEW PHASE =====
                # After tier1+tier2 pass, run a comprehensive review of ALL criteria
                # to catch regressions, bugs, and missed D/T standards.
                final_review_result = self._run_final_review(round_num)
                if final_review_result.get("issues_found"):
                    self.log.warning(
                        f"[final_review] Issues found: {final_review_result['issues_found']} "
                        f"regressions, {final_review_result.get('dt_failures', 0)} D/T failures. "
                        f"Running final fix round."
                    )
                    # Run one final fix round
                    fix_result = self._run_final_fix_round(round_num, final_review_result)
                    # Update final score with fix results
                    score = fix_result.get("score", score)
                    contract_rate = fix_result.get("contract_rate", contract_rate)
                    sprint_rate = fix_result.get("sprint_rate", sprint_rate)
                else:
                    self.log.info("[final_review] No issues found — project is clean.")
                
                log_final_stats(self.log, self.round_stats, self.sprint_score_history,
                               self.overall_score_history, self.token_totals)
                self._clear_state()
                # FIX: Final browser cleanup on successful exit
                try:
                    from tools.playwright_mcp import close_all_sessions_sync
                    close_all_sessions_sync()
                    self.log.info("[browser_cleanup] Final cleanup on success")
                except Exception as e:
                    self.log.debug(f"[browser_cleanup] Final cleanup skipped: {e}")
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
        # FIX: Final browser cleanup on failure/timeout exit
        try:
            from tools.playwright_mcp import close_all_sessions_sync
            close_all_sessions_sync()
            self.log.info("[browser_cleanup] Final cleanup on exit")
        except Exception as e:
            self.log.debug(f"[browser_cleanup] Final cleanup skipped: {e}")
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
                # FIX: Try to restore feature_groups state from saved state
                saved_state = self.state_mgr.load()
                fg_state = saved_state.get("feature_groups_state") if saved_state else None
                if fg_state:
                    self.feature_groups = FeatureGroupState.from_dict(fg_state, groups)
                    self.log.info(
                        f"[feature_groups] Restored {len(groups)} groups, "
                        f"resuming at {self.feature_groups.current_group_id}"
                    )
                else:
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

        # FIX: Force restart dev server BEFORE Builder starts, so Builder's
        # browser_check calls work correctly. Previously dev server was only
        # available in Phase 2 (after Builder), causing Builder's browser_check
        # to fail with "localhost" empty page.
        from tools_impl import _kill_dev_server, start_dev_server
        from harness.build import _detect_project_port
        self.log.info("[dev_server] Restarting dev server before Builder...")
        _kill_dev_server()
        time.sleep(2)  # Wait for port release
        
        # Start fresh dev server for Builder to use
        pkg = self.workspace / "package.json"
        if pkg.exists():
            port = _detect_project_port(self.workspace)
            wait = max(getattr(config, 'DEV_SERVER_DEFAULT_WAIT', 8), 15)
            result = start_dev_server("npm run dev", port=port, wait=wait)
            if not result.startswith("[error]"):
                self.log.info(f"[dev_server] Started on port {port}")
            else:
                self.log.warning(f"[dev_server] Failed to start: {result}")
        time.sleep(1)

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
        runner.add_stage(DesignLintStage)
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

        # BuildGate 通过但可能有条件渲染警告 —— 注入 shared_state 让 Builder 下轮看到
        if build_result and build_result.success:
            payload = build_result.payload or {}
            warnings = payload.get("conditional_render_warnings", [])
            if warnings:
                # 将 violations 加入 known_pitfalls，Builder 下轮自动从 prompt 注入中看到
                violation_summary = "; ".join(warnings[:3])
                self.shared_state.add_pitfall(
                    pitfall=f"条件渲染使 DOM 元素不可见（BuildGate 检测到 {len(warnings)} 处）",
                    solution=f"将 {{condition && <Element />}} 改为 <Element style={{{{display: condition ? 'block' : 'none'}}}} />。具体位置: {violation_summary}",
                    round=round_num,
                    agent="BuildGate",
                )
                self.shared_state.save(str(self.workspace))
                self.log.info(f"[build_gate] Injected {len(warnings)} conditional render warnings into shared_state")

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

        # ===== Restart dev server BEFORE Reviewer only if Builder modified files =====
        # Builder's file writes trigger auto-restart via _restart_dev_server_if_running().
        # Only restart if the server is not responding or if explicitly needed.
        pkg = self.workspace / "package.json"
        if pkg.exists():
            from tools_impl import _kill_dev_server, start_dev_server
            from harness.build import _detect_project_port
            import urllib.request
            
            port = _detect_project_port(self.workspace)
            dev_server_ok = False
            try:
                req = urllib.request.Request(f"http://localhost:{port}", method="HEAD")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    dev_server_ok = resp.status == 200
            except Exception:
                pass
            
            if not dev_server_ok:
                self.log.info("[dev_server] Dev server not responding, restarting before Reviewer...")
                _kill_dev_server()
                time.sleep(2)
                wait = max(getattr(config, 'DEV_SERVER_DEFAULT_WAIT', 8), 15)
                result = start_dev_server("npm run dev --force", port=port, wait=wait)
                if not result.startswith("[error]"):
                    self.log.info(f"[dev_server] Restarted on port {port} for Reviewer")
                else:
                    self.log.warning(f"[dev_server] Restart failed: {result}")
            else:
                self.log.info("[dev_server] Dev server healthy, skipping restart before Reviewer")

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
        """判断是否需要运行 PreBuildGate。
        
        纯 HTML 项目不需要环境检查（没有 package.json）。
        """
        pkg = self.workspace / "package.json"
        # Pure HTML project
        if not pkg.exists():
            return False
        nm = self.workspace / "node_modules"
        return not nm.exists()

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
        # FIX: overall_score should be the true global overall, not the current group rate
        overall_for_log = self.feature_groups.overall_rate() if self.feature_groups else contract_rate
        log_round_stats(self.log, round_num, contract_rate, sprint_rate, overall_for_log,
                       round_prompt, round_completion, elapsed)
        self.dashboard.update_scores(sprint_rate, contract_rate)
        self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])
        return {"sprint_rate": sprint_rate, "contract_rate": contract_rate, "score": contract_rate}

    def _calculate_reviewer_budget(self, round_num: int) -> tuple[int, str]:
        """自适应验证深度：根据功能组复杂度动态调整 Reviewer 预算。
        
        注意：Reviewer 验证的是新功能组，每个功能组都需要独立的完整验证，
        不应过度依赖 Builder 历史表现（Builder 成功率只影响策略提示，不影响预算）。
        
        Returns:
            (max_iterations, strategy_hint): Reviewer 的迭代限制和策略提示
        """
        # 基础预算
        base_budget = 15
        
        # 根据 Builder 历史成功率生成策略提示（不影响预算）
        builder_success_rate = 0.0
        if self.sprint_pass_rate_history:
            recent = self.sprint_pass_rate_history[-3:]
            builder_success_rate = sum(recent) / len(recent)
        
        # 根据功能组复杂度调整预算
        group_complexity = 1.0
        criteria_count = 5  # default
        if self.feature_groups and self.feature_groups.current_group:
            criteria_count = len(self.feature_groups.current_group.get("criteria", []))
            # 标准越多，需要更多验证时间
            group_complexity = 1.0 + (criteria_count - 3) * 0.15
        
        # 计算自适应预算
        # 每个功能组都需要独立的完整验证，预算主要由复杂度决定
        # 第一轮给更多预算（需要熟悉项目），后续轮次稳定
        round_factor = 1.3 if round_num == 1 else 1.0
        adaptive_budget = int(base_budget * group_complexity * round_factor)
        
        # FIX: Increase Reviewer budget range (20-50) to prevent INCOMPLETE reviews
        # Layered validation requires more iterations for contract tests + DevTools + browser
        adaptive_budget = max(25, min(adaptive_budget, 50))
        
        # 生成策略提示
        if builder_success_rate >= 0.8:
            strategy_hint = (
                "Builder 近期表现优秀（成功率 {:.0%}），验证可聚焦于：\n"
                "1. 快速确认核心功能是否存在\n"
                "2. 重点检查边界条件和错误处理\n"
                "3. 无需过度验证已实现模式"
            ).format(builder_success_rate)
        elif builder_success_rate >= 0.5:
            strategy_hint = (
                "Builder 表现中等（成功率 {:.0%}），标准验证深度：\n"
                "1. 完整验证所有验收标准\n"
                "2. 代码审查优先于浏览器交互测试（React 受控组件无法程序化触发）\n"
                "3. 检查代码完整性和非存根实现"
            ).format(builder_success_rate)
        else:
            strategy_hint = (
                "Builder 近期表现不佳（成功率 {:.0%}），需要深度验证：\n"
                "1. 逐条验证每个验收标准（优先代码审查）\n"
                "2. 额外检查条件渲染和状态管理\n"
                "3. 验证事件处理函数是否非存根\n"
                "4. 检查是否有回归问题\n"
                "5. 限制 browser_check 调用次数（最多 3 次），避免在 React 受控组件上浪费迭代"
            ).format(builder_success_rate)
        
        self.log.info(
            f"[adaptive_review] Round {round_num} | "
            f"builder_rate={builder_success_rate:.0%} | "
            f"complexity={group_complexity:.1f} | "
            f"budget={adaptive_budget} | round_factor={round_factor:.1f}"
        )
        
        return adaptive_budget, strategy_hint

    def _run_evaluation(self, round_num: int, round_start: float, build_usage: dict, strategy: dict) -> dict:
        """运行 Reviewer 评估阶段（Reviewer 直接生成 feedback.md）+ 分层验证。"""
        contract_ref = config.CONTRACT_FILE
        current_group_id = None
        if self.feature_groups and self.feature_groups.current_group:
            current_group_id = self.feature_groups.current_group_id

        # =====================================================================
        #  Step 1: Reviewer（自主测试 + 直接生成 feedback）
        # =====================================================================
        self.log.info("Evaluate phase — Step 1: Reviewer (autonomous review + feedback)")
        self.dashboard.start_agent("Reviewer")

        # Inject current feature group into Reviewer task
        group_section = ""
        if self.feature_groups and self.feature_groups.current_group:
            cg = self.feature_groups.current_group
            group_section = (
                f"\n当前验证大组: {cg['id']} — {cg['name']}\n"
                f"验收标准范围: {cg['id']} 的 {len(cg['criteria'])} 项标准\n"
                f"你只验证这个大组的标准，不要检查其他大组。\n"
            )
            # 添加子功能列表
            sub_features = cg.get("sub_features", [])
            if sub_features:
                group_section += "\n组内子功能（按此顺序实现）：\n"
                for i, sub in enumerate(sub_features, 1):
                    group_section += f"{i}. {sub['name']} ({len(sub['criteria'])} 项标准)\n"
        
        # 自适应验证预算
        reviewer_budget, strategy_hint = self._calculate_reviewer_budget(round_num)

        # Reviewer 自主测试任务
        review_task = (
            f"Review the codebase and test the web app for round {round_num}.\n"
            f"{group_section}"
            f"{strategy_hint}\n\n"
            f"验证步骤（由你自主执行）：\n"
            f"1. 调用 detect_framework() 检测项目类型\n"
            f"2. 调用 check_console_logs() 进行健康检查\n"
            f"3. 读取 contract.md 当前大组标准\n"
            f"4. 读取相关源码文件（最多 8 个）\n"
            f"5. 按需调用测试工具（contract_test_run, browser_check, react_devtools_inspect, run_diagnostics）\n"
            f"6. 综合所有测试结果，写 review report 和 feedback\n\n"
            f"Produce TWO outputs:\n"
            f"   a) Save detailed review report to `.eval_cache/round_{round_num}_review.md`\n"
            f"   b) Save feedback for Builder to `{config.FEEDBACK_FILE}` (workspace root)\n"
            f"      The feedback must include GROUP_PASS_RATE, CRITICAL_BUG status, Passed/Failed list, and actionable fix guidance.\n"
            f"Limit: {reviewer_budget} iterations max."
        )
        review_result, review_usage = self.reviewer.run_with_stats(review_task, max_iterations=reviewer_budget)

        # Detect incomplete/error status
        reviewer_status = "success"
        if "[REVIEWER STATUS: INCOMPLETE" in review_result:
            self.log.warning(
                f"[reviewer] Round {round_num} review is INCOMPLETE (max iterations)."
            )
            reviewer_status = "incomplete"
        elif review_result.startswith("[error]"):
            self.log.error(f"[reviewer] Round {round_num} review failed: {review_result[:200]}")
            reviewer_status = "error"

        self.eval_cache.save_round(round_num, review_result)
        self.dashboard.end_agent(reviewer_status)

        # ------------------------------------------------------------------ #
        #  Parse pass rates from feedback.md (generated by Reviewer)
        # ------------------------------------------------------------------ #
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = review_result
        else:
            # Fallback: try to parse from review result if feedback.md not written
            eval_text = review_result
            self.log.warning(f"[reviewer] feedback.md not found at {feedback_path}, parsing from review result")

        # Feature-group driven scoring
        group_rate, overall_rate = parse_group_pass_rates(eval_text)
        sprint_rate, contract_rate = parse_pass_rates(eval_text)
        if group_rate is not None:
            contract_rate = group_rate
        if overall_rate is not None:
            pass
        elif contract_rate is None:
            contract_rate = 0.0
        if sprint_rate is None:
            sprint_rate = contract_rate if contract_rate is not None else 0.0

        # 检测 CRITICAL_BUG
        # 注意：需要区分 "CRITICAL_BUG: 无" (无bug) 和 "CRITICAL_BUG: 有/YES" (有bug)
        has_critical_bug = False
        # 先检查明确的 bug 标记（是/有/yes/true）
        # 使用 .*? 允许中间有其他字符（如 emoji、警告符号等）
        critical_bug_positive_patterns = [
            r'CRITICAL_BUG\s*[:：]\s*.*?(?:有|是|yes|true|存在|found|detected)',
            r'CRITICAL\s*BUG\s*[:：]\s*.*?(?:有|是|yes|true|存在|found|detected)',
            r'CRITICAL_BUG\s*[=＝]\s*(?:yes|true|1)',
        ]
        # 再检查明确的 无bug 标记，避免误报
        critical_bug_negative_patterns = [
            r'CRITICAL_BUG\s*[:：]\s*.*?(?:无|否|no|false|不存在|none|0)',
            r'CRITICAL\s*BUG\s*[:：]\s*.*?(?:无|否|no|false|不存在|none|0)',
        ]
        
        has_positive = any(re.search(p, eval_text, re.IGNORECASE) for p in critical_bug_positive_patterns)
        has_negative = any(re.search(p, eval_text, re.IGNORECASE) for p in critical_bug_negative_patterns)
        
        if has_positive and not has_negative:
            has_critical_bug = True
            self.log.warning(f"[reviewer] CRITICAL_BUG detected in feedback — group cannot advance")
        elif has_negative:
            self.log.info(f"[reviewer] CRITICAL_BUG explicitly marked as 'none' — no bug")

        # ------------------------------------------------------------------ #
        #  Cross-validate with contract.md true denominator
        # ------------------------------------------------------------------ #
        contract_path = self.workspace / config.CONTRACT_FILE
        if contract_path.exists():
            contract_text = contract_path.read_text(encoding="utf-8", errors="replace")
            review_text = self.eval_cache.get_full_report(round_num) or ""
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
                reviewer_reported_total = true_passed + true_failed + true_skipped
                if abs(contract_rate - true_rate) > 0.05 or reviewer_reported_total < total_contract:
                    self.log.warning(
                        f"[reviewer] Reported {contract_rate:.0%} ({reviewer_reported_total} criteria), "
                        f"but true rate over {total_contract} criteria is {true_rate:.0%}. "
                        f"Using true rate."
                    )
                    contract_rate = true_rate
                if overrides:
                    for ov in overrides:
                        self.log.warning(f"[reviewer] {ov}")
        else:
            self.log.warning("[reviewer] contract.md not found, cannot verify true denominator")

        # Detect excessive SKIP ratio
        skip_rate = parse_skip_rate(eval_text)
        if skip_rate > 0.20 and contract_rate >= config.CONTRACT_PASS_RATE_THRESHOLD:
            adjusted_contract = contract_rate * (1 - skip_rate)
            self.log.warning(
                f"[reviewer] SKIP ratio is {skip_rate:.0%}. "
                f"Adjusting contract rate to {adjusted_contract:.0%} to prevent premature termination."
            )
            contract_rate = adjusted_contract

        # Handle Reviewer incomplete/error status
        if reviewer_status == "incomplete":
            # Reviewer incomplete but may have produced partial feedback
            # Trust the feedback if it exists, otherwise cap
            if not feedback_path.exists():
                capped_contract = min(contract_rate, 0.70)
                capped_sprint = min(sprint_rate, 0.70)
                if capped_contract < contract_rate or capped_sprint < sprint_rate:
                    self.log.warning(
                        f"[reviewer] INCOMPLETE and no feedback.md produced. "
                        f"Soft-capping to {capped_contract:.0%} / {capped_sprint:.0%}."
                    )
                contract_rate = capped_contract
                sprint_rate = capped_sprint
        elif reviewer_status == "error":
            # Reviewer hit an actual error
            if contract_rate > 0 or sprint_rate > 0:
                self.log.warning(
                    f"[reviewer] Reviewer ERROR but contract_rate={contract_rate:.0%}. "
                    f"Forcing to 0%."
                )
            contract_rate = 0.0
            sprint_rate = 0.0

        # =====================================================================
        #  大组模式：直接信任 Reviewer 的判定，不做权重聚合
        # =====================================================================
        # Reviewer 已经综合了代码审查、浏览器测试、自动化测试的结果
        # Harness 直接信任 Reviewer 的 GROUP_PASS_RATE 和 CRITICAL_BUG 标记
        
        # 记录 Reviewer 的判定结果
        self.log.info(
            f"[reviewer_result] {current_group_id}: "
            f"rate={contract_rate:.0%}, "
            f"critical_bug={has_critical_bug}"
        )

        # ------------------------------------------------------------------ #
        #  Update feature-group state (大组模式)
        # ------------------------------------------------------------------ #
        if self.feature_groups and self.feature_groups.current_group:
            gid = self.feature_groups.current_group_id
            self.feature_groups.update_rate(gid, contract_rate, has_critical_bug=has_critical_bug)
            
            # Advance to next group if current group passed
            if self.feature_groups.check_should_advance():
                advanced = self.feature_groups.advance()
                if advanced:
                    self.log.info(
                        f"[feature_groups] {gid} passed — advancing to "
                        f"{self.feature_groups.current_group_id}"
                    )
            elif has_critical_bug:
                self.log.warning(
                    f"[feature_groups] {gid} has CRITICAL_BUG — "
                    f"staying on current group for fix"
                )
            else:
                self.log.info(
                    f"[feature_groups] {gid} not passed ({contract_rate:.0%}) — "
                    f"staying for another round"
                )
            
            self.log.info(
                f"[overall] {self.feature_groups.overall_rate():.0%} "
                f"({self.feature_groups.current_group_id} at {contract_rate:.0%}, "
                f"critical_bug={has_critical_bug})"
            )

        self.sprint_pass_rate_history.append(sprint_rate)
        self.contract_pass_rate_history.append(contract_rate)
        # Back-compat
        self.sprint_score_history.append(sprint_rate)
        self.overall_score_history.append(contract_rate)
        self.score_history.append(contract_rate)
        self.log.info(f"  Group pass rate: {contract_rate:.0%} | Overall: {self.feature_groups.overall_rate():.0%}" if self.feature_groups else f"  Sprint pass rate: {sprint_rate:.0%} | Contract pass rate: {contract_rate:.0%}")

        round_prompt = build_usage["prompt"] + review_usage["prompt"]
        round_completion = build_usage["completion"] + review_usage["completion"]
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
        # FIX: overall_score should be the true global overall, not the current group rate
        overall_for_log = self.feature_groups.overall_rate() if self.feature_groups else contract_rate
        log_round_stats(self.log, round_num, contract_rate, sprint_rate, overall_for_log,
                       round_prompt, round_completion, elapsed)
        self.dashboard.update_scores(sprint_rate, contract_rate)
        self.dashboard.update_tokens(self.token_totals["prompt"], self.token_totals["completion"])

        self.log.info(
            f"[round_budget] Round {round_num} complete | "
            f"elapsed: {elapsed:.0f}s | "
            f"tokens: {round_prompt}p+{round_completion}c | "
            f"total: {self.token_totals['prompt']}p+{self.token_totals['completion']}c"
        )
        
        # Update shared state with round findings
        self._update_shared_state_after_round(
            round_num, contract_rate, has_critical_bug, current_group_id
        )

        # FIX: Close browser sessions after each round to prevent Chrome process accumulation
        try:
            from tools.playwright_mcp import close_all_sessions_sync
            close_all_sessions_sync()
            self.log.info("[browser_cleanup] All browser sessions closed after round")
        except Exception as e:
            self.log.debug(f"[browser_cleanup] Cleanup skipped: {e}")

        return {"sprint_rate": sprint_rate, "contract_rate": contract_rate, "score": contract_rate}

    def _update_shared_state_after_round(
        self, round_num: int, contract_rate: float, has_critical_bug: bool, group_id: str | None
    ) -> None:
        """每轮结束后更新共享状态"""
        if not hasattr(self, 'shared_state') or self.shared_state is None:
            return
        
        # Update basic info
        self.shared_state.total_rounds = round_num
        self.shared_state.current_group = group_id or ""
        
        # Extract pitfalls from feedback
        feedback_path = self.workspace / config.FEEDBACK_FILE
        if feedback_path.exists():
            try:
                feedback_text = feedback_path.read_text(encoding="utf-8", errors="replace")
                # Extract failed criteria as pitfalls
                if "Failed" in feedback_text or "FAIL" in feedback_text:
                    # Look for CRITICAL_BUG description
                    if has_critical_bug:
                        bug_match = re.search(r'CRITICAL_BUG[:：]\s*(.+?)(?:\n|$)', feedback_text, re.IGNORECASE)
                        if bug_match:
                            bug_desc = bug_match.group(1).strip()
                            # Extract solution from feedback if available
                            solution_match = re.search(r'修复[建议|方案]?:\s*(.+?)(?:\n|$)', feedback_text)
                            solution = solution_match.group(1).strip() if solution_match else "See feedback.md"
                            self.shared_state.add_pitfall(
                                pitfall=bug_desc,
                                solution=solution,
                                round=round_num,
                                agent="Reviewer",
                            )
                
                # Extract verification shortcuts
                if contract_rate >= 0.9:
                    self.shared_state.add_verified_pattern(
                        pattern=f"{group_id} implementation",
                        context=f"Round {round_num}",
                        result="PASS",
                    )
                    
            except Exception as e:
                self.log.debug(f"[shared_state] Failed to extract from feedback: {e}")
        
        # Save shared state
        try:
            self.shared_state.save(str(self.workspace))
            self.log.debug(f"[shared_state] Saved after round {round_num}")
        except Exception as e:
            self.log.debug(f"[shared_state] Save failed: {e}")

    def _extract_tech_stack_from_spec(self, spec_text: str) -> None:
        """从 spec.md 提取技术选型到共享状态
        
        支持多种格式：
        - Markdown 表格: | Framework | React |
        - 列表项: - Framework: React
        - 标题: ## Tech Stack\n- React
        - 行内: Framework: React
        """
        if not hasattr(self, 'shared_state') or self.shared_state is None:
            return
        
        import re
        
        # === 1. 提取技术栈（多格式支持）===
        tech_patterns = {
            "framework": [
                r'(?:Framework|前端框架|框架)[\s:：]\s*([^\n|]+?)(?:\n|\||$)',
                r'\|\s*(?:Framework|框架)\s*\|\s*([^|]+?)\s*\|',
                r'[-*]\s*(?:Framework|框架)[\s:：]\s*([^\n]+?)(?:\n|$)',
            ],
            "state_management": [
                r'(?:State Management|状态管理)[\s:：]\s*([^\n|]+?)(?:\n|\||$)',
                r'\|\s*(?:State|状态管理)\s*\|\s*([^|]+?)\s*\|',
                r'[-*]\s*(?:State|状态管理)[\s:：]\s*([^\n]+?)(?:\n|$)',
            ],
            "styling": [
                r'(?:Styling|样式|CSS)[\s:：]\s*([^\n|]+?)(?:\n|\||$)',
                r'\|\s*(?:Styling|样式)\s*\|\s*([^|]+?)\s*\|',
                r'[-*]\s*(?:Styling|样式)[\s:：]\s*([^\n]+?)(?:\n|$)',
            ],
            "build_tool": [
                r'(?:Build Tool|构建工具|Bundler)[\s:：]\s*([^\n|]+?)(?:\n|\||$)',
                r'\|\s*(?:Build|构建)\s*\|\s*([^|]+?)\s*\|',
                r'[-*]\s*(?:Build|构建工具)[\s:：]\s*([^\n]+?)(?:\n|$)',
            ],
            "language": [
                r'(?:Language|语言)[\s:：]\s*([^\n|]+?)(?:\n|\||$)',
                r'\|\s*(?:Language|语言)\s*\|\s*([^|]+?)\s*\|',
                r'[-*]\s*(?:Language|语言)[\s:：]\s*([^\n]+?)(?:\n|$)',
            ],
        }
        
        for key, patterns in tech_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, spec_text, re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    # 清理 Markdown 格式
                    value = re.sub(r'\*\*|\*|`|\[|\]', '', value)
                    if value and len(value) < 100:
                        self.shared_state.tech_stack[key] = value
                        break  # 找到第一个匹配就停止
        
        # === 2. 从 Tech Stack 表格中提取（常见格式）===
        # | 类别 | 技术 |
        # |------|------|
        # | Framework | React |
        table_pattern = r'\|\s*(?:Tech|技术|类别|Type)\s*\|\s*(?:选择|技术|Technology|Value)\s*\|[\s\n\-]+((?:\|[^\n]+\|\n?)+)'
        table_match = re.search(table_pattern, spec_text, re.IGNORECASE)
        if table_match:
            table_content = table_match.group(1)
            for line in table_content.split('\n'):
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if len(cells) >= 2:
                    key_map = {
                        'framework': 'framework',
                        '前端框架': 'framework',
                        'state': 'state_management',
                        '状态管理': 'state_management',
                        'styling': 'styling',
                        '样式': 'styling',
                        'css': 'styling',
                        'build': 'build_tool',
                        '构建': 'build_tool',
                        'bundler': 'build_tool',
                        'language': 'language',
                        '语言': 'language',
                    }
                    key_lower = cells[0].lower()
                    mapped_key = None
                    for k, v in key_map.items():
                        if k in key_lower:
                            mapped_key = v
                            break
                    if mapped_key:
                        self.shared_state.tech_stack[mapped_key] = cells[1]
        
        # === 3. 提取约束（多格式支持）===
        constraint_patterns = [
            r'(?:Constraint|约束|必须|Must)[\s:：]\s*([^\n]+?)(?:\n|$)',
            r'[-*]\s*(?:Constraint|约束)[\s:：]\s*([^\n]+?)(?:\n|$)',
            r'\|\s*(?:Constraint|约束)\s*\|\s*([^|]+?)\s*\|',
        ]
        for pattern in constraint_patterns:
            for match in re.finditer(pattern, spec_text, re.IGNORECASE):
                constraint = match.group(1).strip()
                # 过滤掉太短的和已存在的
                if len(constraint) > 10 and len(constraint) < 200 and constraint not in self.shared_state.constraints:
                    self.shared_state.constraints.append(constraint)
        
        # 去重并限制数量
        self.shared_state.constraints = list(dict.fromkeys(self.shared_state.constraints))[-15:]
        
        # === 4. 提取颜色/设计约束（特殊处理）===
        color_pattern = r'(?:Color|颜色|Palette|配色)[\s:：]\s*([^\n]+?)(?:\n|$)'
        for match in re.finditer(color_pattern, spec_text, re.IGNORECASE):
            color = match.group(1).strip()
            if color and color not in self.shared_state.constraints:
                self.shared_state.constraints.append(f"Color: {color}")
        
        # === 5. 保存 ===
        try:
            self.shared_state.save(str(self.workspace))
            self.log.info(f"[shared_state] Extracted tech_stack: {self.shared_state.tech_stack}")
            self.log.info(f"[shared_state] Extracted {len(self.shared_state.constraints)} constraints")
        except Exception:
            pass

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
            task += f"\n总体进度: {self.feature_groups.overall_rate():.0%}\n"
            # 注入当前大组的状态
            current_gid = self.feature_groups.current_group_id
            current_rate = self.feature_groups.pass_rates.get(current_gid, 0.0)
            has_bug = self.feature_groups.critical_bugs.get(current_gid, False)
            threshold = GROUP_PASS_THRESHOLD_DEFAULT
            task += f"\n当前大组 {current_gid} 通过率: {current_rate:.0%} (通过阈值: {threshold:.0%})\n"
            if has_bug:
                task += f"⚠️ 当前大组有 CRITICAL_BUG，必须先修复才能进入下一个大组。\n"
            elif current_rate < threshold:
                task += f"注意: {current_gid} 尚未达标，请继续修复未通过项。\n"
            else:
                task += f"注意: {current_gid} 已达标，可以推进到下一个大组。\n"

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

    def _inject_iteration_budget(self, build_task: str) -> str:
        """从 sprint.md 解析预估迭代数，动态注入预算提示。
        
        自适应预算：根据功能组复杂度、历史表现和项目类型调整。
        """
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
        
        # 自适应调整：根据功能组复杂度
        complexity_multiplier = 1.0
        if self.feature_groups and self.feature_groups.current_group:
            criteria_count = len(self.feature_groups.current_group.get("criteria", []))
            # 标准越多，预算越多
            complexity_multiplier = 1.0 + (criteria_count - 3) * 0.1
        
        # 自适应调整：根据项目类型
        project_type_multiplier = 1.0
        pkg = self.workspace / "package.json"
        if not pkg.exists():
            # 纯 HTML 项目更简单，预算减少
            project_type_multiplier = 0.8
        
        # 自适应调整：根据历史成功率
        history_multiplier = 1.0
        if self.sprint_pass_rate_history:
            recent_rate = sum(self.sprint_pass_rate_history[-3:]) / len(self.sprint_pass_rate_history[-3:])
            if recent_rate >= 0.8:
                history_multiplier = 0.9  # 表现好，稍微收紧
            elif recent_rate < 0.5:
                history_multiplier = 1.2  # 表现差，给更多空间
        
        adjusted = int(conservative * complexity_multiplier * project_type_multiplier * history_multiplier)
        conservative = max(15, min(adjusted, 40))  # 限制范围
        
        threshold = int(conservative * 0.8)
        hard_limit = int(conservative * 1.2)
        
        self.log.info(
            f"[adaptive_budget] base={conservative} | "
            f"complexity={complexity_multiplier:.1f} | "
            f"type={project_type_multiplier:.1f} | "
            f"history={history_multiplier:.1f} | "
            f"final={conservative}"
        )
        
        budget_msg = f"""
## Iteration Budget（自适应限制）
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
        """基于项目复杂度、历史表现、Builder 策略动态调整轮数上限

        融合三个方案：
        1. 硬上限提高到 15（原 10/12）
        2. runtime 奖励可部分突破 base 上限（soft cap = hard + 3）
        3. 按剩余组数保底，确保不会在做完前就停止
        """
        hard_limit = getattr(config, 'MAX_ROUNDS_HARD', 15)

        # 方案 1: 基础估算（受硬上限限制）
        base = min(self._estimate_from_spec(), hard_limit)

        # 方案 2: 动态调整（runtime 奖励可突破 base，但受 soft cap 限制）
        runtime_adjust = self._runtime_adjustment()
        strategy_adjust = self._strategy_adjustment()

        # 方案 3: 按剩余组数保底
        remaining_estimate = 0
        if self.feature_groups:
            total_groups = len(self.feature_groups.group_ids)
            completed = sum(
                1 for gid in self.feature_groups.group_ids
                if self.feature_groups.check_group_passed(gid)
            )
            remaining = total_groups - completed
            # 已跑轮数 + 剩余组数 × 2（每大组平均 2 轮）+ 2轮缓冲
            remaining_estimate = self._completed_rounds + remaining * 2 + 2

        # 融合计算
        max_rounds = base + runtime_adjust + strategy_adjust
        max_rounds = max(max_rounds, remaining_estimate)   # 保底：至少够做完剩余组
        max_rounds = min(max_rounds, hard_limit + 3)       # soft cap: 硬上限+3轮奖励空间
        max_rounds = max(max_rounds, getattr(config, 'MIN_ROUNDS', 3))

        self.log.info(
            f"[dynamic_rounds] base={base}, runtime={runtime_adjust}, "
            f"strategy={strategy_adjust}, remaining={remaining_estimate}, "
            f"hard={hard_limit} -> max_rounds={max_rounds}"
        )
        return max_rounds

    def _estimate_from_spec(self) -> int:
        """从 spec.md 解析功能点，估算基础轮数
        
        修复：更准确地估算功能点数量，考虑 Phase 分层
        """
        spec_path = self.workspace / config.SPEC_FILE
        if not spec_path.exists():
            return 5

        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        
        # 更精确的功能计数：统计 Group N 或 F1, F2, ... 格式的功能编号
        import re
        # 新格式: Group 1, Group 2, ...
        group_matches = re.findall(r'Group\s+\d+[:：]', spec_text)
        # 旧格式: F1, F2, ...
        feature_matches = re.findall(r'\*\*F\d+[:：]', spec_text)
        
        group_count = len(group_matches)
        feature_count = len(feature_matches)
        
        # 使用大组数量优先
        if group_count > 0:
            # 大组模式：每个大组约 1-3 轮（实现 + 修复）
            rounds = group_count * 2  # 每个大组平均 2 轮
        else:
            # 旧格式：每 Phase 至少 1 轮 + 每个功能约 1 轮
            phase_matches = re.findall(r'### Phase \d+', spec_text)
            phase_count = len(phase_matches)
            rounds = phase_count + feature_count
        
        # 统计图片资源需求
        asset_count = len(re.findall(r'generate_image|hero-gradient|theme-.*-preview|empty-state', spec_text))
        rounds += asset_count // 3   # 图片生成每 3 张约 1 轮
        
        # 保底：至少 4 轮，但不超过硬上限
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
        # FIX: 只有当当前功能组真正 stuck 时才缩减。避免把"连续通过不同组"
        # （contract_rate 都是 100%）误判为停滞。
        if len(self.contract_pass_rate_history) >= 3:
            last_three = self.contract_pass_rate_history[-3:]
            if max(last_three) - min(last_three) < 0.05:  # 5 percentage points
                if self.feature_groups:
                    current_gid = self.feature_groups.current_group_id
                    stuck_count = self.feature_groups.stuck_counts.get(current_gid, 0)
                    if stuck_count >= 2:
                        return -1
                else:
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
        
        使用动态 tier 划分：根据实际功能组数量自适应。
        """
        if not self.feature_groups:
            # Legacy mode: use contract rate threshold
            if self.contract_pass_rate_history:
                rate = self.contract_pass_rate_history[-1]
                if rate >= config.CONTRACT_PASS_RATE_THRESHOLD:
                    return True, f"Contract pass rate {rate:.0%} >= threshold"
                return False, f"Contract pass rate {rate:.0%} below threshold"
            return False, "No scores yet"

        return _check_exit_condition_dynamic(self.feature_groups)

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

    # ------------------------------------------------------------------ #
    #      Final Review Phase                                           #
    # ------------------------------------------------------------------ #
    def _run_final_review(self, round_num: int) -> dict:
        """Run a comprehensive final review of ALL criteria after tier1+tier2 pass.
        
        This catches regressions, bugs, and missed D/T standards that were
        skipped during the per-group build-eval loop.
        
        Returns:
            dict with keys:
                - issues_found: int (number of regressions found)
                - dt_failures: int (number of D/T criteria failures)
                - final_contract_rate: float
                - final_overall_rate: float
                - review_report_path: Path to saved review report
        """
        self.log.info("\n" + "="*60)
        self.log.info("FINAL REVIEW PHASE")
        self.log.info("="*60)
        
        contract_ref = config.CONTRACT_FILE
        
        # Step 1: Reviewer — comprehensive review of ALL groups
        self.log.info("Final Review — Step 1: Reviewer (full contract review)")
        self.dashboard.start_agent("Reviewer")
        
        # Build a comprehensive review task covering ALL functional groups
        all_groups_text = ""
        if self.feature_groups:
            for g in self.feature_groups.groups:
                gid = g["id"]
                rate = self.feature_groups.pass_rates.get(gid, 0.0)
                all_groups_text += f"- {gid} {g['name']}: previously scored {rate:.0%}\n"
        
        review_task = (
            f"FINAL REVIEW — Round {round_num}.\n\n"
            f"This is a COMPREHENSIVE review of the ENTIRE project after all functional "
            f"groups have been implemented. Your job is to find REGRESSIONS, BUGS, and "
            f"issues that were missed during per-group development.\n\n"
            f"Previously implemented groups:\n{all_groups_text}\n"
            f"Read {contract_ref}, then:\n"
            f"1. Test ALL major features — not just the current group.\n"
            f"2. Look for: broken interactions, missing elements, console errors, "
            f"   visual glitches, responsive issues, accessibility problems.\n"
            f"3. Check D/T (Design/Technical) criteria that were skipped earlier.\n"
            f"4. **全局设计一致性检查**：\n"
            f"   - 检查所有组件文件中按钮圆角/阴影/边框是否统一\n"
            f"   - 检查配色方案是否遵循 Design Direction（统计主色使用次数）\n"
            f"   - 检查空状态是否都有 Lucide 图标 + 引导文字（不是纯文本）\n"
            f"   - 检查 Lucide 图标使用一致性（无 emoji、无内联 SVG）\n"
            f"5. **跨大组交互验证**：\n"
            f"   - G1 的状态管理是否被 G2/G3 正确消费\n"
            f"   - 路由/导航是否覆盖所有已实现的大组\n"
            f"   - 全局样式（Tailwind config）是否被所有组件正确引用\n"
            f"6. Report ONLY real bugs and regressions — do not re-test passing features.\n"
            f"Limit: 30 iterations max."
        )
        
        review_result, review_usage = self.reviewer.run_with_stats(review_task, max_iterations=30)
        
        # Save final review report
        final_review_path = self.workspace / ".eval_cache" / f"round_{round_num}_final_review.md"
        try:
            final_review_path.write_text(review_result, encoding="utf-8")
        except Exception:
            pass
        
        reviewer_status = "success"
        if "[REVIEWER STATUS: INCOMPLETE" in review_result:
            reviewer_status = "incomplete"
        elif review_result.startswith("[error]"):
            reviewer_status = "error"
            self.log.warning("[final_review] Reviewer error — continuing with code review only")
        
        self.dashboard.end_agent(reviewer_status)
        
        # Parse final review results from feedback.md (generated by Reviewer)
        feedback_path = self.workspace / config.FEEDBACK_FILE
        eval_text = ""
        if feedback_path.exists():
            try:
                eval_text = feedback_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                eval_text = review_result
        else:
            eval_text = review_result
        
        # Extract metrics from Reviewer output
        issues_found = 0
        dt_failures = 0
        final_contract_rate = 0.0
        
        import re as _re
        m = _re.search(r"FINAL_PASS_RATE[:：]\s*(\d+)%", eval_text)
        if m:
            final_contract_rate = int(m.group(1)) / 100.0
        else:
            _, final_contract_rate = parse_pass_rates(eval_text)
            if final_contract_rate is None:
                final_contract_rate = 0.0
        
        m = _re.search(r"REGRESSIONS?[:：]\s*(\d+)", eval_text)
        if m:
            issues_found = int(m.group(1))
        else:
            issues_found = eval_text.lower().count("regression")
        
        m = _re.search(r"DT_PASS_RATE[:：]\s*(\d+)%", eval_text)
        if m:
            dt_rate = int(m.group(1)) / 100.0
            dt_failures = max(0, int((1.0 - dt_rate) * 10))
        
        m = _re.search(r"BUGS_FOUND[:：]\s*(\d+)", eval_text)
        if m:
            issues_found = max(issues_found, int(m.group(1)))
        
        # FIX: Detect critical/blocker issues from Reviewer feedback
        # Check for critical keywords that indicate serious bugs
        critical_keywords = [
            "CRITICAL_BLOCKER", "CRITICAL", "BLOCKER", 
            "infinite loop", "Maximum update depth exceeded",
            "app crashes", "app does not render", "fails to render",
            "white screen", "blank screen", "crash on load"
        ]
        critical_count = 0
        eval_lower = eval_text.lower()
        for keyword in critical_keywords:
            if keyword.lower() in eval_lower:
                critical_count += 1
        
        # If critical issues found, ensure issues_found is at least 1
        if critical_count > 0 and issues_found == 0:
            issues_found = critical_count
            self.log.warning(
                f"[final_review] Detected {critical_count} critical issues from Reviewer feedback "
                f"(keywords: {[k for k in critical_keywords if k.lower() in eval_lower]})"
            )
        
        self.log.info(
            f"[final_review] Complete: {issues_found} issues, "
            f"{dt_failures} D/T failures, {final_contract_rate:.0%} final rate"
        )
        
        return {
            "issues_found": issues_found,
            "dt_failures": dt_failures,
            "final_contract_rate": final_contract_rate,
            "final_overall_rate": final_contract_rate,
            "review_report_path": str(final_review_path),
        }

    def _run_final_fix_round(self, round_num: int, final_review: dict) -> dict:
        """Run one final Builder round to fix issues found in final review.
        
        Args:
            round_num: The round number that triggered final review
            final_review: Result dict from _run_final_review()
            
        Returns:
            dict with updated score metrics after fix round
        """
        self.log.info("\n" + "="*60)
        self.log.info("FINAL FIX ROUND")
        self.log.info("="*60)
        
        # Write a special sprint.md for the fix round
        fix_sprint_path = self.workspace / config.SPRINT_FILE
        fix_sprint_content = (
            f"# Final Fix Round\n\n"
            f"## Goal\n"
            f"Fix critical issues found in the final review.\n\n"
            f"## Issues to Fix\n"
            f"- Regressions found: {final_review.get('issues_found', 0)}\n"
            f"- D/T failures: {final_review.get('dt_failures', 0)}\n"
            f"- Final pass rate before fix: {final_review.get('final_contract_rate', 0):.0%}\n\n"
            f"## Instructions\n"
            f"1. Read feedback.md for the final review findings.\n"
            f"2. Focus on CRITICAL and MAJOR bugs first.\n"
            f"3. Fix regressions in previously-passing functional groups.\n"
            f"4. Address D/T (Design/Technical) criteria if feasible.\n"
            f"5. Do NOT refactor or rewrite — only fix specific issues.\n"
            f"6. Validate build passes after each change.\n"
        )
        try:
            fix_sprint_path.write_text(fix_sprint_content, encoding="utf-8")
        except Exception:
            pass
        
        # Build fix task for Builder
        rollback_msg = (
            f"\n\nFINAL FIX ROUND: The project passed all functional tiers, but "
            f"the final review found {final_review.get('issues_found', 0)} issues. "
            f"Read feedback.md for specific bugs to fix. Focus on critical issues only."
        )
        
        build_task = self._build_build_task(round_num, rollback_msg)
        
        # Run Builder
        self.log.info(f"[Builder] Agent starting (final fix)")
        self.dashboard.start_agent("Builder")
        build_result, build_usage = self.builder.run_with_stats(build_task)
        self.dashboard.end_agent("success")
        
        # Parse Builder strategy
        strategy = parse_strategy(build_result)
        self.strategy_history.append({"round": round_num, **strategy})
        
        # Pipeline: Build validation
        self.log.info("[pipeline] Phase 2 — Validation & commit (final fix)")
        runner = PipelineRunner(self.workspace, self.event_bus)
        runner.add_stage(BuildGateStage)
        runner.add_stage(DevServerGateStage)
        runner.add_stage(ScreenshotGateStage)
        runner.add_stage(GitCommitStage)
        context = runner.run(round_num)
        
        build_result_gate = context.get("build_gate")
        if build_result_gate and not build_result_gate.success:
            self.log.warning("[final_fix] Build failed — returning original scores")
            return {
                "score": final_review.get("final_contract_rate", 0.0),
                "contract_rate": final_review.get("final_contract_rate", 0.0),
                "sprint_rate": final_review.get("final_contract_rate", 0.0),
            }
        
        # Re-run evaluation to get updated scores
        return self._run_evaluation(round_num, time.time(), build_usage, strategy)
