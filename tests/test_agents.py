"""Tests for Agent core logic."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
import json

import config


@pytest.fixture(autouse=True)
def mock_openai_client(monkeypatch):
    """Prevent real OpenAI client initialization."""
    monkeypatch.setattr("agents.client", MagicMock())


@pytest.fixture
def agent_instance(tmp_path, monkeypatch):
    """Create an Agent with mocked dependencies."""
    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(config, "COMPRESS_THRESHOLD", 1000)
    monkeypatch.setattr(config, "RESET_THRESHOLD", 2000)
    monkeypatch.setattr(config, "AGENT_ITERATION_LIMITS", {"test_agent": 5})

    from agents import Agent
    return Agent(
        name="TestAgent",
        system_prompt="You are a test agent.",
        tools=[],
        logger=MagicMock(),
    )


class TestRunWithStats:
    def test_returns_content_when_no_tool_calls(self, agent_instance):
        """Agent should return LLM content when no tools are called."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Final answer"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        with patch("agents.client.chat.completions.create", return_value=mock_response):
            text, usage = agent_instance.run_with_stats("Do something")

        assert text == "Final answer"
        assert usage["prompt"] == 10
        assert usage["completion"] == 5

    def test_executes_tool_calls(self, agent_instance):
        """Agent should execute tools and return final result."""
        # First response: tool call
        tool_call_response = MagicMock()
        tool_call_response.choices = [MagicMock()]
        tool_call_response.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "read_file"
        tc.function.arguments = json.dumps({"path": "test.txt"})
        tc.id = "tc_1"
        tool_call_response.choices[0].message.tool_calls = [tc]
        tool_call_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        # Second response: final answer
        final_response = MagicMock()
        final_response.choices = [MagicMock()]
        final_response.choices[0].message.content = "Done"
        final_response.choices[0].message.tool_calls = None
        final_response.usage = MagicMock(prompt_tokens=15, completion_tokens=5)

        with patch("agents.client.chat.completions.create", side_effect=[tool_call_response, final_response]):
            with patch("tools.execute_tool", return_value="file content"):
                text, usage = agent_instance.run_with_stats("Read a file")

        assert text == "Done"
        assert usage["prompt"] == 25  # 10 + 15

    def test_respects_max_iterations(self, agent_instance):
        """Agent should stop after max_iterations."""
        # Always return tool calls to force iteration
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "read_file"
        tc.function.arguments = json.dumps({"path": "test.txt"})
        tc.id = "tc_1"
        mock_response.choices[0].message.tool_calls = [tc]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        with patch("agents.client.chat.completions.create", return_value=mock_response):
            with patch("tools.execute_tool", return_value="content"):
                text, usage = agent_instance.run_with_stats("Loop forever")

        assert "Max iterations reached" in text or "INCOMPLETE" in text

    def test_handles_llm_error(self, agent_instance):
        """Agent should return error when LLM call fails."""
        with patch("agents.client.chat.completions.create", side_effect=Exception("API down")):
            text, usage = agent_instance.run_with_stats("Do something")

        assert text.startswith("[error]")
        assert "API down" in text

    def test_env_fix_budget_for_builder(self, tmp_path, monkeypatch):
        """Builder should detect consecutive env-fix calls and force PIVOT."""
        monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
        monkeypatch.setattr(config, "COMPRESS_THRESHOLD", 1000)
        monkeypatch.setattr(config, "RESET_THRESHOLD", 2000)
        monkeypatch.setattr(config, "AGENT_ITERATION_LIMITS", {"builder": 10})

        from agents import Agent
        builder = Agent(
            name="Builder",
            system_prompt="You are a builder.",
            tools=[],
            logger=MagicMock(),
        )

        # Always return npm install tool calls
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "run_bash"
        tc.function.arguments = json.dumps({"command": "npm install"})
        tc.id = "tc_1"
        mock_response.choices[0].message.tool_calls = [tc]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        with patch("agents.client.chat.completions.create", return_value=mock_response):
            with patch("tools.execute_tool", return_value="installed"):
                text, usage = builder.run_with_stats("Fix env")

        assert "PIVOT" in text
        assert "environment-fix" in text.lower() or "env-fix" in text.lower()
