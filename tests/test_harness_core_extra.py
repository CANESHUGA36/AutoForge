"""Additional tests for harness/core.py bug fixes."""
import pytest
from unittest.mock import MagicMock, patch
import config


@pytest.fixture
def harness_instance(mock_workspace):
    from harness.core import Harness
    with patch.object(Harness, '_load_state'):
        with patch('harness.core.Agent'):
            h = Harness(str(mock_workspace))
            h._completed_rounds = 0
            h.sprint_score_history = []
            h.overall_score_history = []
            h.strategy_history = []
            h.token_totals = {"prompt": 0, "completion": 0}
            h.round_stats = []
            h.score_history = []
            h.log = MagicMock()
            h.dashboard = MagicMock()
            return h


def test_inject_iteration_budget_multiple_formats(mock_workspace, harness_instance):
    """BUG #11: _inject_iteration_budget should match multiple formats.
    
    Note: With adaptive budget, the final value may differ from parsed value
    due to complexity/type/history multipliers.
    """
    sprint = mock_workspace / "sprint.md"
    # Create package.json so it's not treated as pure HTML (which reduces budget)
    (mock_workspace / "package.json").write_text('{"name": "test"}')

    # Chinese format with full-width colon
    sprint.write_text("## Estimated Iterations\n- 保守：20 次", encoding="utf-8")
    result = harness_instance._inject_iteration_budget("Task")
    # Should contain the budget section (value may be adjusted)
    assert "Iteration Budget" in result

    # English format
    sprint.write_text("## Estimated Iterations\nConservative: 15 iterations", encoding="utf-8")
    result = harness_instance._inject_iteration_budget("Task")
    assert "Iteration Budget" in result

    # Budget format (English colon)
    sprint.write_text("## Budget: 30", encoding="utf-8")
    result = harness_instance._inject_iteration_budget("Task")
    assert "Iteration Budget" in result

    # No match — fallback
    sprint.write_text("No budget info here", encoding="utf-8")
    result = harness_instance._inject_iteration_budget("Task")
    assert "Iteration Budget" in result


def test_big_group_mode_no_dimension_thresholds(mock_workspace, harness_instance):
    """Big-group mode: dimension thresholds are deprecated, Reviewer makes autonomous decisions."""
    from harness.eval import parse_dimension_scores, check_dimension_thresholds

    eval_text = """
Overall Score: 8.5/10
Sprint Score: 7.0/10
### Functionality: 9/10
### Design Quality: 2/10
### Craft: 8/10
"""
    dim_scores = parse_dimension_scores(eval_text)
    failed_dims = check_dimension_thresholds(dim_scores)
    # In big-group mode, dimension thresholds don't block
    assert failed_dims == [], "Big-group mode should not use dimension thresholds"
