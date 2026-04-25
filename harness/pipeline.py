"""Pipeline 框架：Stage-based 执行引擎。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import time
import logging

from harness.events import EventBus, Event

log = logging.getLogger("harness")


@dataclass
class StageResult:
    """阶段执行结果。"""
    success: bool
    message: str = ""
    payload: dict = field(default_factory=dict)  # 传递给下游阶段的数据
    auto_fix_attempted: bool = False  # 是否尝试过自动修复
    should_skip_remaining: bool = False  # 是否跳过剩余阶段
    score: float | None = None  # 如果阶段产生分数


class PipelineStage(ABC):
    """Pipeline 阶段的抽象基类。"""

    name: str = ""
    max_retries: int = 0  # 失败时自动重试次数
    allow_auto_fix: bool = False  # 是否允许自动修复
    timeout_seconds: int = 3600  # 阶段超时

    def __init__(self, workspace: Path, event_bus: EventBus, round_num: int):
        self.workspace = workspace
        self.event_bus = event_bus
        self.round_num = round_num

    def execute(self) -> StageResult:
        """执行阶段：包装为事件。"""
        self._emit("stage_started")
        start = time.time()

        try:
            result = self.run()
            elapsed = time.time() - start
            result.payload["elapsed_s"] = elapsed

            if result.success:
                self._emit("stage_completed", result.payload)
            else:
                self._emit("stage_failed", {"message": result.message, **result.payload})

                # 自动修复逻辑
                if self.allow_auto_fix and not result.auto_fix_attempted:
                    fix_result = self.auto_fix()
                    if fix_result and fix_result.success:
                        self._emit("stage_auto_fixed", fix_result.payload)
                        return fix_result

            return result

        except Exception as e:
            log.exception(f"Stage {self.name} crashed")
            self._emit("stage_crashed", {"error": str(e)})
            return StageResult(success=False, message=f"Stage crashed: {e}")

    @abstractmethod
    def run(self) -> StageResult:
        """子类实现具体逻辑。"""
        pass

    def auto_fix(self) -> StageResult | None:
        """子类可选择实现自动修复逻辑。"""
        return None

    def _emit(self, event_type: str, payload: dict | None = None) -> None:
        self.event_bus.emit(Event(
            type=event_type,
            stage=self.name,
            round_num=self.round_num,
            payload=payload or {},
        ))


class PipelineRunner:
    """Pipeline 运行器：按顺序执行 Stage，处理分支逻辑。"""

    def __init__(self, workspace: Path, event_bus: EventBus):
        self.workspace = workspace
        self.event_bus = event_bus
        self.stages: list[type[PipelineStage]] = []

    def add_stage(self, stage_class: type[PipelineStage]) -> "PipelineRunner":
        self.stages.append(stage_class)
        return self

    def run(self, round_num: int) -> dict:
        """执行完整 Pipeline。"""
        context: dict[str, Any] = {"round_num": round_num}

        for stage_class in self.stages:
            stage = stage_class(self.workspace, self.event_bus, round_num)
            result = stage.execute()

            # 保存阶段结果到上下文
            context[stage.name] = result

            # 如果阶段要求跳过剩余阶段
            if result.should_skip_remaining:
                log.info(f"[{stage.name}] Requested skip_remaining, halting pipeline")
                break

            # 如果阶段失败且没有自动修复
            if not result.success and not result.auto_fix_attempted:
                log.warning(f"[{stage.name}] Failed and no auto-fix, halting pipeline")
                break

        return context
