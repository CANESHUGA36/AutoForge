"""Tests for Pipeline framework (events, pipeline, stages)."""
from __future__ import annotations

import pytest
from pathlib import Path

from harness.events import EventBus, Event
from harness.pipeline import PipelineRunner, PipelineStage, StageResult


# --------------------------------------------------------------------------- #
#  Dummy stages for testing
# --------------------------------------------------------------------------- #

class SuccessStage(PipelineStage):
    name = "success"

    def run(self) -> StageResult:
        return StageResult(success=True, message="ok", payload={"value": 42})


class FailStage(PipelineStage):
    name = "fail"

    def run(self) -> StageResult:
        return StageResult(success=False, message="failed", payload={"error": "x"})


class SkipStage(PipelineStage):
    name = "skip"

    def run(self) -> StageResult:
        return StageResult(success=False, message="skip", should_skip_remaining=True)


class CrashStage(PipelineStage):
    name = "crash"

    def run(self) -> StageResult:
        raise RuntimeError("boom")


class AutoFixStage(PipelineStage):
    name = "autofix"
    allow_auto_fix = True

    def run(self) -> StageResult:
        return StageResult(
            success=False,
            message="needs fix",
            payload={"fixable": True}
        )

    def auto_fix(self) -> StageResult:
        return StageResult(success=True, message="fixed", payload={"fixed": True})


class CounterStage(PipelineStage):
    name = "counter"
    _call_count = 0

    def run(self) -> StageResult:
        CounterStage._call_count += 1
        return StageResult(success=True, message=f"call #{CounterStage._call_count}")


# --------------------------------------------------------------------------- #
#  EventBus tests
# --------------------------------------------------------------------------- #

def test_event_bus_emit_and_get(mock_workspace):
    bus = EventBus(mock_workspace)
    e = Event(type="test", stage="s1", round_num=1, payload={"k": "v"})
    bus.emit(e)

    events = bus.get_events()
    assert len(events) == 1
    assert events[0].type == "test"
    assert events[0].stage == "s1"


def test_event_bus_filter_by_stage(mock_workspace):
    bus = EventBus(mock_workspace)
    bus.emit(Event(type="a", stage="s1", round_num=1))
    bus.emit(Event(type="a", stage="s2", round_num=1))

    assert len(bus.get_events(stage="s1")) == 1
    assert len(bus.get_events(stage="s2")) == 1
    assert len(bus.get_events()) == 2


def test_event_bus_get_latest(mock_workspace):
    bus = EventBus(mock_workspace)
    bus.emit(Event(type="started", stage="s1", round_num=1))
    bus.emit(Event(type="completed", stage="s1", round_num=1))

    latest = bus.get_latest("s1", "completed")
    assert latest is not None
    assert latest.type == "completed"

    missing = bus.get_latest("s1", "unknown")
    assert missing is None


def test_event_bus_persistence(mock_workspace):
    bus = EventBus(mock_workspace)
    bus.emit(Event(type="persist", stage="s1", round_num=1, payload={"data": 123}))

    log_path = mock_workspace / ".events" / "pipeline.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    import json
    assert json.loads(lines[0])["type"] == "persist"


def test_event_bus_subscriber(mock_workspace):
    bus = EventBus(mock_workspace)
    received = []

    def handler(event):
        received.append(event.type)

    bus.subscribe("my_event", handler)
    bus.emit(Event(type="my_event", stage="s1", round_num=1))
    bus.emit(Event(type="other", stage="s1", round_num=1))

    assert received == ["my_event"]


# --------------------------------------------------------------------------- #
#  PipelineRunner tests
# --------------------------------------------------------------------------- #

def test_pipeline_runs_stages_sequentially(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(SuccessStage).add_stage(CounterStage)

    ctx = runner.run(1)

    assert ctx["success"].success is True
    assert ctx["success"].payload["value"] == 42
    assert ctx["counter"].success is True
    assert "call #" in ctx["counter"].message


def test_pipeline_halts_on_failure(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(FailStage).add_stage(SuccessStage)

    ctx = runner.run(1)

    assert "fail" in ctx
    assert "success" not in ctx  # halted before second stage
    assert ctx["fail"].success is False


def test_pipeline_skips_remaining(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(SkipStage).add_stage(SuccessStage)

    ctx = runner.run(1)

    assert "skip" in ctx
    assert "success" not in ctx
    assert ctx["skip"].should_skip_remaining is True


def test_pipeline_crashed_stage(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(CrashStage).add_stage(SuccessStage)

    ctx = runner.run(1)

    assert "crash" in ctx
    assert ctx["crash"].success is False
    assert "boom" in ctx["crash"].message
    assert "success" not in ctx


def test_pipeline_auto_fix(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(AutoFixStage)

    ctx = runner.run(1)

    assert ctx["autofix"].success is True
    assert ctx["autofix"].message == "fixed"


def test_pipeline_emits_events(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(SuccessStage)

    runner.run(1)

    started = bus.get_events(stage="success", event_type="stage_started")
    completed = bus.get_events(stage="success", event_type="stage_completed")
    assert len(started) == 1
    assert len(completed) == 1
    assert completed[0].payload["value"] == 42


def test_pipeline_failure_emits_events(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    runner.add_stage(FailStage)

    runner.run(1)

    failed = bus.get_events(stage="fail", event_type="stage_failed")
    assert len(failed) == 1
    assert failed[0].payload["error"] == "x"


def test_pipeline_context_includes_round_num(mock_workspace):
    bus = EventBus(mock_workspace)
    runner = PipelineRunner(mock_workspace, bus)
    ctx = runner.run(5)
    assert ctx["round_num"] == 5


# --------------------------------------------------------------------------- #
#  StageResult dataclass
# --------------------------------------------------------------------------- #

def test_stage_result_defaults():
    r = StageResult(success=True)
    assert r.message == ""
    assert r.payload == {}
    assert r.auto_fix_attempted is False
    assert r.should_skip_remaining is False
    assert r.score is None


def test_stage_result_with_score():
    r = StageResult(success=True, score=8.5)
    assert r.score == 8.5
