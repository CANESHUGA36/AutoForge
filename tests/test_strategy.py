from harness.strategy import parse_strategy


def test_parse_strategy_refine():
    text = "---\nSTRATEGY: REFINE\nREASON: Fixing minor bugs"
    result = parse_strategy(text)
    assert result["strategy"] == "REFINE"
    assert result["reason"] == "Fixing minor bugs"


def test_parse_strategy_pivot():
    text = "---\nSTRATEGY: PIVOT\nREASON: Architecture is wrong\nNEW DIRECTION: Use Redux"
    result = parse_strategy(text)
    assert result["strategy"] == "PIVOT"
    assert result["new_direction"] == "Use Redux"


def test_parse_strategy_unknown_defaults_to_refine():
    text = "No strategy here"
    result = parse_strategy(text)
    assert result["strategy"] == "REFINE"
