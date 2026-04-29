"""Tests for feature_groups module."""
from __future__ import annotations

import pytest

from harness.feature_groups import (
    parse_feature_groups,
    FeatureGroupState,
    _compute_tiers,
    _get_group_threshold,
    _check_exit_condition_dynamic,
    TIER_REQUIREMENTS,
)


class TestComputeTiers:
    def test_small_project_single_tier(self):
        tiers = _compute_tiers(["F1", "F2", "F3"])
        assert "tier1" in tiers
        assert "tier2" not in tiers
        assert tiers["tier1"]["min_rate"] == 1.0
        assert tiers["tier1"]["groups"] == ["F1", "F2", "F3"]

    def test_medium_project_two_tiers(self):
        tiers = _compute_tiers([f"F{i}" for i in range(1, 9)])
        assert "tier1" in tiers
        assert "tier2" in tiers
        assert "tier3" not in tiers
        assert len(tiers["tier1"]["groups"]) == 4
        assert len(tiers["tier2"]["groups"]) == 4
        assert tiers["tier2"]["min_rate"] == 0.80

    def test_large_project_three_tiers(self):
        tiers = _compute_tiers([f"F{i}" for i in range(1, 13)])
        assert "tier1" in tiers
        assert "tier2" in tiers
        assert "tier3" in tiers
        assert tiers["tier3"]["min_rate"] == 0.70


class TestParseFeatureGroups:
    def test_heading_format(self):
        text = """
## F1 Audio Upload
- [ ] **F1.1** User can select audio file
- [ ] **F1.2** File size limit is 10MB

## F2 Playback
- [ ] **F2.1** Play button works
- [ ] **F2.2** Pause button works
"""
        groups = parse_feature_groups(text)
        assert len(groups) == 2
        assert groups[0]["id"] == "F1"
        assert groups[0]["name"] == "Audio Upload"
        assert "F1.1" in groups[0]["criteria"]
        assert "F1.2" in groups[0]["criteria"]
        assert groups[1]["id"] == "F2"
        assert "F2.1" in groups[1]["criteria"]

    def test_table_format_fallback(self):
        text = """
| **F1** | Audio Upload | desc |
| **F2** | Playback | desc |
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
            {"id": "F1", "name": "Upload", "criteria": ["F1.1", "F1.2"]},
            {"id": "F2", "name": "Playback", "criteria": ["F2.1"]},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.current_group_id == "F1"
        assert fgs.current_group["name"] == "Upload"
        assert fgs.current_idx == 0

    def test_advance(self):
        groups = [
            {"id": "F1", "name": "A", "criteria": ["F1.1"]},
            {"id": "F2", "name": "B", "criteria": ["F2.1"]},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.advance() is True
        assert fgs.current_group_id == "F2"
        assert fgs.advance() is False
        assert fgs.current_group_id == "F2"  # stays at last

    def test_update_rate_pass(self):
        groups = [{"id": "F1", "name": "A", "criteria": ["F1.1"]}]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("F1", 1.0)
        assert fgs.pass_rates["F1"] == 1.0
        assert fgs.stuck_counts.get("F1", 0) == 0
        assert fgs.check_should_advance() is True

    def test_update_rate_fail_and_stuck(self):
        groups = [{"id": "F1", "name": "A", "criteria": ["F1.1"]}]
        fgs = FeatureGroupState(groups)
        for _ in range(3):
            fgs.update_rate("F1", 0.5)
        assert fgs.stuck_counts["F1"] == 3
        stuck, gid = fgs.any_group_stuck()
        assert stuck is True
        assert gid == "F1"

    def test_overall_rate(self):
        groups = [
            {"id": "F1", "name": "A", "criteria": ["F1.1", "F1.2"]},
            {"id": "F2", "name": "B", "criteria": ["F2.1"]},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("F1", 1.0)
        fgs.update_rate("F2", 0.0)
        # (2*1.0 + 1*0.0) / 3 = 0.667
        assert fgs.overall_rate() == pytest.approx(0.667, abs=0.01)

    def test_is_complete(self):
        groups = [
            {"id": "F1", "name": "A", "criteria": ["F1.1"]},
            {"id": "F2", "name": "B", "criteria": ["F2.1"]},
        ]
        fgs = FeatureGroupState(groups)
        assert fgs.is_complete() is False
        fgs.update_rate("F1", 1.0)
        fgs.update_rate("F2", 1.0)
        assert fgs.is_complete() is True

    def test_to_dict(self):
        groups = [{"id": "F1", "name": "A", "criteria": ["F1.1"]}]
        fgs = FeatureGroupState(groups)
        d = fgs.to_dict()
        assert d["current_group"] == "F1"
        assert "pass_rates" in d
        assert "overall" in d


class TestExitCondition:
    def test_not_complete_when_tier_incomplete(self):
        groups = [
            {"id": "F1", "name": "A", "criteria": ["F1.1"]},
            {"id": "F2", "name": "B", "criteria": ["F2.1"]},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("F1", 1.0)
        fgs.update_rate("F2", 0.0)
        complete, msg = _check_exit_condition_dynamic(fgs)
        assert complete is False
        assert "incomplete" in msg or "below" in msg

    def test_complete_when_all_passed(self):
        groups = [
            {"id": "F1", "name": "A", "criteria": ["F1.1"]},
        ]
        fgs = FeatureGroupState(groups)
        fgs.update_rate("F1", 1.0)
        complete, msg = _check_exit_condition_dynamic(fgs)
        assert complete is True
        assert "All tiers passed" in msg
