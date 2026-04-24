import pytest
from harness.eval import parse_scores, parse_dimension_scores, check_dimension_thresholds


def test_parse_scores_standard_format():
    text = "SPRINT_SCORE: 7.5/10\nOVERALL_SCORE: 6.0/10"
    sprint, overall = parse_scores(text)
    assert sprint == 7.5
    assert overall == 6.0


def test_parse_scores_missing():
    text = "OVERALL_SCORE: 8.0/10"
    sprint, overall = parse_scores(text)
    # Legacy fallback: OVERALL_SCORE alone triggers legacy SCORE match
    assert overall == 8.0


def test_check_dimension_thresholds_functionality_fail():
    scores = {"functionality": 4.0, "design_quality": 5.0}
    failed = check_dimension_thresholds(scores)
    assert "functionality" in [f.split("=")[0] for f in failed]


def test_check_dimension_thresholds_all_pass():
    scores = {"functionality": 6.0, "design_quality": 5.0}
    failed = check_dimension_thresholds(scores)
    assert failed == []
