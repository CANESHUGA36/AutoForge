"""
Dashboard — 实时监控 Harness 执行状态

提供两种模式：
1. 文件监控模式（默认）：读取 workspace 中的状态文件，输出结构化状态
2. HTTP 模式（可选）：启动轻量 HTTP 服务器，提供 JSON API

状态文件：
- harness.log — 主日志
- .events/*.jsonl — Agent 结构化事件
- .workspace_state.json — Workspace 状态
- .eval_cache/round_*_summary.json — 评估摘要
- harness_state.json — Harness 持久化状态
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("harness")


@dataclass
class DashboardState:
    """Dashboard 展示的状态快照"""
    workspace: str = ""
    round_num: int = 0
    phase: str = "idle"  # idle | planning | building | evaluating | done
    
    # 当前 Agent
    current_agent: str = ""
    agent_status: str = ""  # running | success | error | timeout
    agent_elapsed_s: float = 0.0
    
    # 当前轮次进度
    build_progress: str = ""  # 如 "3/5 files written"
    
    # 评分历史
    sprint_scores: list[float] = field(default_factory=list)
    overall_scores: list[float] = field(default_factory=list)
    
    # Token 使用
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    
    # 时间统计
    round_elapsed_s: float = 0.0
    total_elapsed_s: float = 0.0
    
    # 最近事件
    recent_events: list[dict] = field(default_factory=list)
    
    # 错误/告警
    alerts: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "round": self.round_num,
            "phase": self.phase,
            "current_agent": self.current_agent,
            "agent_status": self.agent_status,
            "agent_elapsed_s": round(self.agent_elapsed_s, 1),
            "build_progress": self.build_progress,
            "sprint_scores": self.sprint_scores,
            "overall_scores": self.overall_scores,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "round_elapsed_s": round(self.round_elapsed_s, 1),
            "total_elapsed_s": round(self.total_elapsed_s, 1),
            "recent_events": self.recent_events,
            "alerts": self.alerts,
        }
    
    def to_console(self) -> str:
        """生成控制台友好的状态显示（ASCII 兼容）"""
        lines = [
            "+" + "-" * 58 + "+",
            f"|{' Harness Dashboard':<58}|",
            "+" + "-" * 58 + "+",
        ]
        
        # 基本信息
        ws_name = Path(self.workspace).name if self.workspace else "N/A"
        lines.append(f"| Workspace: {ws_name:<47}|")
        lines.append(f"| Round: {self.round_num:<51}|")
        lines.append(f"| Phase: {self.phase:<51}|")
        lines.append("|" + " " * 58 + "|")
        
        # 当前 Agent
        if self.current_agent:
            status_icon = "OK" if self.agent_status == "success" else ">>" if self.agent_status == "running" else "XX"
            lines.append(f"| Agent: [{status_icon}] {self.current_agent:<47}|")
            lines.append(f"|   Status: {self.agent_status:<46}|")
            lines.append(f"|   Elapsed: {self.agent_elapsed_s:.0f}s{'':<44}|")
        
        lines.append("|" + " " * 58 + "|")
        
        # 评分
        if self.sprint_scores:
            latest_s = self.sprint_scores[-1]
            latest_o = self.overall_scores[-1] if self.overall_scores else 0
            lines.append(f"| Latest Score: Sprint {latest_s:.0%} | Overall {latest_o:.0%}{'':<17}|")
        
        # Token
        total = self.total_prompt_tokens + self.total_completion_tokens
        lines.append(f"| Tokens: {self.total_prompt_tokens:,}p + {self.total_completion_tokens:,}c = {total:,}{'':<12}|")
        
        # 时间
        lines.append(f"| Round time: {self.round_elapsed_s:.0f}s | Total: {self.total_elapsed_s:.0f}s{'':<21}|")
        
        # 告警
        if self.alerts:
            lines.append("|" + " " * 58 + "|")
            lines.append(f"| ! Alerts ({len(self.alerts)}):{'':<42}|")
            for alert in self.alerts[-3:]:
                truncated = alert[:54]
                lines.append(f"|   {truncated:<55}|")
        
        lines.append("+" + "-" * 58 + "+")
        return "\n".join(lines)


class Dashboard:
    """Dashboard 管理器"""
    
    def __init__(self, workspace: str, logger: logging.Logger | None = None):
        self.workspace = Path(workspace)
        self.state = DashboardState(workspace=workspace)
        self._round_start_time: float = 0.0
        self._total_start_time: float = 0.0
        self._agent_start_time: float = 0.0
        self._logger = logger or log
        
    def start_run(self) -> None:
        """标记总运行开始"""
        self._total_start_time = time.time()
        self.state.phase = "starting"
        self._flush()
    
    def start_round(self, round_num: int) -> None:
        """标记新轮次开始"""
        self._round_start_time = time.time()
        self.state.round_num = round_num
        self.state.phase = "planning"
        self.state.agent_status = ""
        self.state.build_progress = ""
        self.state.alerts = []
        self._flush()
    
    def start_agent(self, agent_name: str) -> None:
        """标记 Agent 开始"""
        self._agent_start_time = time.time()
        self.state.current_agent = agent_name
        self.state.agent_status = "running"
        
        # 更新 phase
        if agent_name == "Builder":
            self.state.phase = "building"
        elif agent_name in ("CodeReviewer", "BrowserTester", "Evaluator"):
            self.state.phase = "evaluating"
        
        self._flush()
    
    def end_agent(self, status: str = "success", alert: str | None = None) -> None:
        """标记 Agent 结束"""
        self.state.agent_status = status
        self.state.agent_elapsed_s = time.time() - self._agent_start_time
        if alert:
            self.state.alerts.append(alert)
        self._flush()
    
    def update_scores(self, sprint: float, overall: float) -> None:
        """更新评分"""
        self.state.sprint_scores.append(sprint)
        self.state.overall_scores.append(overall)
        self._flush()
    
    def update_tokens(self, prompt: int, completion: int) -> None:
        """更新 token 统计"""
        self.state.total_prompt_tokens = prompt
        self.state.total_completion_tokens = completion
        self._flush()
    
    def add_alert(self, message: str) -> None:
        """添加告警"""
        self.state.alerts.append(message)
        self._flush()
    
    def add_event(self, event: dict) -> None:
        """添加事件"""
        self.state.recent_events.append(event)
        if len(self.state.recent_events) > 20:
            self.state.recent_events = self.state.recent_events[-20:]
        self._flush()
    
    def set_build_progress(self, progress: str) -> None:
        """更新构建进度"""
        self.state.build_progress = progress
        self._flush()
    
    def end_run(self, success: bool) -> None:
        """标记运行结束"""
        self.state.phase = "done" if success else "failed"
        self.state.current_agent = ""
        self._flush()
    
    def _update_timers(self) -> None:
        """更新计时器"""
        now = time.time()
        if self._round_start_time:
            self.state.round_elapsed_s = now - self._round_start_time
        if self._total_start_time:
            self.state.total_elapsed_s = now - self._total_start_time
        if self._agent_start_time and self.state.agent_status == "running":
            self.state.agent_elapsed_s = now - self._agent_start_time
    
    def _flush(self) -> None:
        """将状态写入日志（不再单独写 .dashboard_state.json，状态已并入 harness_state.json）。"""
        self._update_timers()
        
        # 输出到日志（每轮或关键事件时）
        if self.state.phase in ("done", "failed") or self.state.agent_status in ("error", "timeout"):
            self._logger.info(f"[dashboard]\n{self.state.to_console()}")
    
    def subscribe_to(self, event_bus) -> None:
        """订阅 EventBus 事件，自动更新 Dashboard 状态。"""
        event_bus.subscribe("stage_started", self._on_stage_started)
        event_bus.subscribe("stage_completed", self._on_stage_completed)
        event_bus.subscribe("stage_failed", self._on_stage_failed)
        event_bus.subscribe("stage_crashed", self._on_stage_failed)
        event_bus.subscribe("stage_auto_fixed", self._on_stage_auto_fixed)
    
    def _on_stage_started(self, event) -> None:
        self.add_event({
            "type": "stage_started",
            "stage": event.stage,
            "round": event.round_num,
        })
    
    def _on_stage_completed(self, event) -> None:
        payload = event.payload or {}
        entry = {
            "type": "stage_completed",
            "stage": event.stage,
            "round": event.round_num,
            "elapsed_s": payload.get("elapsed_s"),
        }
        if "score" in payload:
            entry["score"] = payload["score"]
        self.add_event(entry)
    
    def _on_stage_failed(self, event) -> None:
        payload = event.payload or {}
        msg = payload.get("message", "unknown failure")
        self.add_event({
            "type": event.type,
            "stage": event.stage,
            "round": event.round_num,
            "message": msg,
        })
        self.add_alert(f"{event.stage}: {msg}")
    
    def _on_stage_auto_fixed(self, event) -> None:
        self.add_event({
            "type": "stage_auto_fixed",
            "stage": event.stage,
            "round": event.round_num,
        })
    
    def snapshot(self) -> DashboardState:
        """获取当前状态快照"""
        self._update_timers()
        return self.state


def load_dashboard_state(workspace: str) -> DashboardState | None:
    """从 harness_state.json 加载 Dashboard 状态（外部监控用）。
    
    Dashboard 状态已并入 harness_state.json，不再单独维护 .dashboard_state.json。
    """
    import config
    path = Path(workspace) / config.STATE_FILE
    if not path.exists():
        return None
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        dash_data = data.get("dashboard", {})
        if not dash_data:
            return None
        state = DashboardState()
        state.workspace = dash_data.get("workspace", workspace)
        state.round_num = dash_data.get("round", 0)
        state.phase = dash_data.get("phase", "idle")
        state.current_agent = dash_data.get("current_agent", "")
        state.agent_status = dash_data.get("agent_status", "")
        state.agent_elapsed_s = dash_data.get("agent_elapsed_s", 0)
        state.sprint_scores = dash_data.get("sprint_scores", [])
        state.overall_scores = dash_data.get("overall_scores", [])
        state.total_prompt_tokens = dash_data.get("total_prompt_tokens", 0)
        state.total_completion_tokens = dash_data.get("total_completion_tokens", 0)
        state.round_elapsed_s = dash_data.get("round_elapsed_s", 0)
        state.total_elapsed_s = dash_data.get("total_elapsed_s", 0)
        state.alerts = dash_data.get("alerts", [])
        return state
    except Exception:
        return None


def print_dashboard(workspace: str) -> None:
    """打印当前 Dashboard 状态到控制台"""
    state = load_dashboard_state(workspace)
    if state:
        print(state.to_console())
    else:
        print(f"No dashboard state found in {workspace}")
