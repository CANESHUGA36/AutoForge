from context import count_tokens, _safe_split_index


def test_safe_split_index_does_not_cut_tool_chain():
    messages = [
        {"role": "user", "content": "call tool"},
        {"role": "assistant", "content": "...", "tool_calls": [{}]},
        {"role": "tool", "content": "result"},
    ]
    idx = _safe_split_index(messages, 1)
    # 不能切在 assistant(tool_calls) 和 tool 之间
    assert not (messages[idx].get("role") == "assistant" and messages[idx].get("tool_calls"))
