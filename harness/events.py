"""事件总线：所有 Pipeline 阶段间的通信通道。"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import json
import logging

log = logging.getLogger("harness")


@dataclass(frozen=True)
class Event:
    """不可变事件。"""
    type: str           # 事件类型：stage_started, stage_completed, stage_failed, ...
    stage: str          # 触发事件的阶段名
    round_num: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict = field(default_factory=dict)  # 事件携带的数据

    def to_dict(self) -> dict:
        return asdict(self)


class EventBus:
    """事件总线：内存队列 + 持久化日志。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.events_dir = workspace / ".events"
        self.events_dir.mkdir(exist_ok=True)
        self._subscribers: dict[str, list[Callable]] = {}
        self._events: list[Event] = []

    def emit(self, event: Event) -> None:
        """发布事件：持久化 + 通知订阅者。"""
        self._events.append(event)
        # 持久化到 .events/pipeline.jsonl
        self._append_to_log(event)
        # 通知订阅者
        for handler in self._subscribers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                log.warning(f"Event handler failed for {event.type}: {e}")

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    def _append_to_log(self, event: Event) -> None:
        path = self.events_dir / "pipeline.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def get_events(self, stage: str | None = None, event_type: str | None = None) -> list[Event]:
        """查询事件（用于调试和状态重建）。"""
        result = self._events
        if stage:
            result = [e for e in result if e.stage == stage]
        if event_type:
            result = [e for e in result if e.type == event_type]
        return result

    def get_latest(self, stage: str, event_type: str) -> Event | None:
        """获取某个阶段的最新某类事件。"""
        events = self.get_events(stage=stage, event_type=event_type)
        return events[-1] if events else None
