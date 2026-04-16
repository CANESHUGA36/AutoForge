"""
Agent 实现 （含上下文管理）
"""
from __future__ import annotations

import json
import logging
from openai import OpenAI

import config
import context
import skills
from tools import execute_tool

log = logging.getLogger("harness")
client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


class Agent:
    """支持上下文管理的 Agent"""

    def __init__(self, name: str, system_prompt: str, tools: list):
        self.name = name
        catalog = skills.build_catalog_prompt()
        self.system_prompt = system_prompt + (f"\n{catalog}" if catalog else "")
        self.tools = tools

    def run(self, user_prompt: str) -> str:
        """运行 Agent"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        log.info(f"Agent '{self.name}' starting")

        for iteration in range(1, config.MAX_ITERATIONS + 1):
            # 上下文生命周期检查
            messages = self._check_context_lifecycle(messages)

            try:
                response = client.chat.completions.create(
                    model=config.MODEL,
                    messages=messages,
                    tools=self.tools,
                )
            except Exception as e:
                log.error(f"LLM call failed: {e}")
                return f"[error] LLM call failed: {e}"

            message = response.choices[0].message

            if message.content:
                log.debug(f"[{self.name}] {message.content[:100]}...")

            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls] if message.tool_calls else []
            })

            if not message.tool_calls:
                return message.content or "Done"

            for tool_call in message.tool_calls:
                name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                log.debug(f"[{self.name}] Tool: {name}")

                result = execute_tool(name, arguments)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

        return "[error] Max iterations reached"

    def _check_context_lifecycle(self, messages: list[dict]) -> list[dict]:
        """检查并管理上下文生命周期"""
        token_count = context.count_tokens(messages)

        if token_count > config.RESET_THRESHOLD or context.detect_anxiety(messages):
            log.warning(f"Context reset triggered ({token_count} tokens)")
            checkpoint = context.create_checkpoint(messages, self._llm_call_simple)
            return context.restore_from_checkpoint(checkpoint, self.system_prompt)
        elif token_count > config.COMPRESS_THRESHOLD:
            log.info(f"Context compaction triggered ({token_count} tokens)")
            return context.compact_messages(messages, self._llm_call_simple, self.name.lower())

        return messages

    def _llm_call_simple(self, messages: list[dict]) -> str:
        """简单的 LLM 调用（用于摘要）"""
        response = client.chat.completions.create(
            model=config.MODEL,
            messages=messages,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
