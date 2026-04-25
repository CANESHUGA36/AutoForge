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
    # Use F1, F2 format and Phase headers to match new regex-based counting
    spec.write_text(
        "### Phase 1\n\n" +
        "\n".join([f"**F{i}: Feature {i}** — user story — acceptance" for i in range(1, 10)]) +
        "\n\n### Phase 2\n\n" +
        "\n".join([f"**F{i+9}: Feature {i+9}** — user story — acceptance" for i in range(1, 4)])
    )
    max_r = harness_instance._estimate_from_spec()
    # 2 phases + 12 features//2 + 0 assets//3 = 2 + 6 + 0 = 8, min 3 = 8
    assert max_r >= 5


def test_runtime_adjustment_stagnation(mock_workspace, harness_instance):
    harness_instance.sprint_pass_rate_history = [0.50, 0.51, 0.52]
    harness_instance.contract_pass_rate_history = [0.50, 0.51, 0.52]
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
