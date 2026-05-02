"""Tests for feature_groups module (大组模式)."""
from __future__ import annotations

import pytest

from harness.feature_groups import (
    parse_feature_groups,
    FeatureGroupState,
    _check_exit_condition_dynamic,
    GROUP_PASS_THRESHOLD_DEFAULT,
    OVERALL_PASS_THRESHOLD,
)


class TestParseFeatureGroups:
    """测试新的大组格式解析."""

    def test_new_group_format(self):
        text = """
# Acceptance Criteria

## Group 1: Core Canvas

### Infinite Canvas
- [ ] **G1.A.1** Middle mouse button drag pans the canvas viewport
- [ ] **G1.A.2** Space + left mouse drag also pans the canvas

### Shape Drawing
- [ ] **G1.B.1** Rectangle tool creates rectangle on click-drag
- [ ] **G1.B.2** Holding Shift while drawing rectangle constrains to square

## Group 2: Content Tools

### Text Boxes
- [ ] **G2.A.1** Text tool click on canvas creates new text box
- [ ] **G2.A.2** Double-clicking text box enters edit mode
"""
        groups = parse_feature_groups(text)
        assert len(groups) == 2
        assert groups[0]["id"] == "G1"
        assert groups[0]["name"] == "Core Canvas"
        assert "G1.A.1" in groups[0]["criteria"]
        assert "G1.A.2" in groups[0]["criteria"]
        assert "G1.B.1" in groups[0]["criteria"]
        assert groups[1]["id"] == "G2"
        assert "G2.A.1" in groups[1]["criteria"]
        # 检查子功能
        assert len(groups[0]["sub_features"]) == 2
        assert groups[0]["sub_features"][0]["name"] == "Infinite Canvas"
        assert len(groups[0]["sub_features"][0]["criteria"]) == 2

    def test_legacy_format_fallback(self):
        """测试旧格式 F-group 的向后兼容."""
        text = """
### F1 Audio Upload
- [ ] **F1.1** User can select audio file
- [ ] **F1.2** File size limit is 10MB

### F2 Playback
- [ ] **F2.1** Play button works
- [ ] **F2.2** Pause button works
"""
        groups = parse_feature_groups(text)
        assert len(groups) == 2
        assert groups[0]["id"] == "F1"
        assert "F1.1" in groups[0]["criteria"]

    def test_empty_contract(self):
        groups = parse_feature_groups("")
        assert groups == []


class TestFeatureGroupState:
    def test_initial_state(self):
        groups = [
            {"id": "G1", "name": "Core", "criteria": ["G1.A.1", "G1.A.2"], "sub_features": []},
            {"id": "G2", "name": "Content", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.current_group_id == "G1"
        assert fgs.current_group["name"] == "Core"
        assert fgs.current_idx == 0

    def test_advance(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
            {"id": "G2", "name": "B", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.advance() is True
        assert fgs.current_group_id == "G2"
        assert fgs.advance() is False
        assert fgs.current_group_id == "G2"  # stays at last

    def test_update_rate_pass(self):
        groups = [{"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []}]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=False)
        assert fgs.pass_rates["G1"] == 1.0
        assert fgs.stuck_counts.get("G1", 0) == 0
        assert fgs.critical_bugs.get("G1", False) is False
        assert fgs.check_should_advance() is True

    def test_update_rate_with_critical_bug(self):
        """测试 CRITICAL_BUG 阻止推进."""
        groups = [{"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []}]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=True)
        assert fgs.pass_rates["G1"] == 1.0
        assert fgs.critical_bugs["G1"] is True
        assert fgs.check_should_advance() is False  # 有 bug 不能推进

    def test_update_rate_fail_and_stuck(self):
        groups = [{"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []}]
        fgs = FeatureGroupState(groups)
        for _ in range(3):
            fgs.update_rate("G1", 0.5, has_critical_bug=False)
        assert fgs.stuck_counts["G1"] == 3
        stuck, gid = fgs.any_group_stuck()
        assert stuck is True
        assert gid == "G1"

    def test_overall_rate(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1", "G1.A.2"], "sub_features": []},
            {"id": "G2", "name": "B", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=False)
        fgs.update_rate("G2", 0.0, has_critical_bug=False)
        # (2*1.0 + 1*0.0) / 3 = 0.667
        assert fgs.overall_rate() == pytest.approx(0.667, abs=0.01)

    def test_is_complete(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
            {"id": "G2", "name": "B", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.is_complete() is False
        fgs.update_rate("G1", 1.0, has_critical_bug=False)
        fgs.update_rate("G2", 1.0, has_critical_bug=False)
        assert fgs.is_complete() is True

    def test_is_complete_with_critical_bug(self):
        """测试有 CRITICAL_BUG 时不算完成."""
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=True)
        assert fgs.is_complete() is False  # 有 bug 不算完成

    def test_to_dict(self):
        groups = [{"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []}]
        fgs = FeatureGroupState(groups)
        d = fgs.to_dict()
        assert d["current_group"] == "G1"
        assert "pass_rates" in d
        assert "critical_bugs" in d
        assert "overall" in d

    def test_from_dict(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
            {"id": "G2", "name": "B", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 0.8, has_critical_bug=True)
        fgs.advance()
        
        data = fgs.to_dict()
        restored = FeatureGroupState.from_dict(data, groups)
        assert restored.current_group_id == "G2"
        assert restored.pass_rates["G1"] == 0.8
        assert restored.critical_bugs["G1"] is True


class TestExitCondition:
    def test_not_complete_when_group_incomplete(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
            {"id": "G2", "name": "B", "criteria": ["G2.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=False)
        fgs.update_rate("G2", 0.0, has_critical_bug=False)
        complete, msg = _check_exit_condition_dynamic(fgs)
        assert complete is False
        assert "not passed" in msg or "below" in msg

    def test_not_complete_with_critical_bug(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=True)
        complete, msg = _check_exit_condition_dynamic(fgs)
        assert complete is False
        assert "critical" in msg.lower() or "not passed" in msg

    def test_complete_when_all_passed(self):
        groups = [
            {"id": "G1", "name": "A", "criteria": ["G1.A.1"], "sub_features": []},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("G1", 1.0, has_critical_bug=False)
        complete, msg = _check_exit_condition_dynamic(fgs)
        assert complete is True
