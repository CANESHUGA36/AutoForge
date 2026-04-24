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
            return h


def test_estimate_from_spec_features(mock_workspace, harness_instance):
    spec = mock_workspace / "spec.md"
    spec.write_text("\n".join([f"- Feature {i}: ..." for i in range(9)]))
    max_r = harness_instance._estimate_from_spec()
    assert max_r >= 5  # 9 个功能至少 2 + 9//3 = 5 轮


def test_runtime_adjustment_stagnation(mock_workspace, harness_instance):
    harness_instance.sprint_score_history = [5.0, 5.1, 5.2]
    harness_instance.overall_score_history = [5.0, 5.1, 5.2]
    assert harness_instance._runtime_adjustment() == -1


def test_strategy_adjustment_double_pivot(mock_workspace, harness_instance):
    harness_instance.strategy_history = [
        {"strategy": "PIVOT", "reason": "bad"},
        {"strategy": "PIVOT", "reason": "still bad"},
    ]
    assert harness_instance._strategy_adjustment() == 2


def test_calculate_max_rounds_respects_bounds(mock_workspace, harness_instance):
    harness_instance.overall_score_history = []
    harness_instance.strategy_history = []
    max_r = harness_instance._calculate_max_rounds()
    assert max_r >= config.MIN_ROUNDS
    assert max_r <= config.MAX_ROUNDS_HARD
