"""
Agent 实现 （含上下文管理 + 结构化日志 + 异步执行）
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

import config
import context
import skills
from tools import execute_tool
from workspace_state import WorkspaceState, inject_state_into_messages

# P2: Agent 级异步执行支持
_AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent_")

log = logging.getLogger("harness")
client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


@dataclass
class AgentRunLog:
    """单次 Agent 运行的结构化日志，便于后续分析和监控。"""
    agent_name: str
    start_time: float = field(default_factory=time.time)
    iterations: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    final_status: str = "running"
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "elapsed_s": round(time.time() - self.start_time, 1),
            "iterations": len(self.iterations),
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "status": self.final_status,
            "error": self.error,
            "tool_summary": [
                {"name": t["name"], "status": t["status"], "latency_ms": t["latency_ms"]}
                for t in self.tool_calls
            ],
        }

    def emit(self) -> None:
        """输出结构化日志行。"""
        # 使用模块级 log，因为 AgentRunLog 没有绑定到特定 Agent 实例
        log.info(f"[agent_summary] {json.dumps(self.to_dict(), ensure_ascii=False)}")

    def write_jsonl(self, workspace: str) -> None:
        """追加写入 workspace 的 events 目录，供外部监控。"""
        events_dir = Path(workspace) / ".events"
        events_dir.mkdir(exist_ok=True)
        path = events_dir / f"{self.agent_name}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict(), ensure_ascii=False, default=str) + "\n")


class Agent:
    """支持上下文管理的 Agent"""

    def __init__(self, name: str, system_prompt: str, tools: list, use_state: bool = False, logger=None):
        self.name = name
        catalog = skills.build_catalog_prompt()
        self.system_prompt = system_prompt + (f"\n{catalog}" if catalog else "")
        self.tools = tools
        self.use_state = use_state
        self._workspace_state: WorkspaceState | None = None
        self._log = logger or log

    def run(self, user_prompt: str) -> str:
        """Run the agent and return the final text response."""
        text, _ = self.run_with_stats(user_prompt)
        return text

    def run_async(self, user_prompt: str, timeout: int = 3600):
        """异步运行 Agent，返回 Future 对象。
        
        用于 Harness 并行执行多个 Agent（如 CodeReviewer ∥ BrowserTester）。
        调用方可以用 future.result(timeout=...) 等待结果。
        """
        return _AGENT_EXECUTOR.submit(self.run_with_stats, user_prompt)

    def run_with_stats(self, user_prompt: str, max_iterations: int = None) -> tuple[str, dict]:
        """Run the agent and return (final_text, usage_dict).

        增强日志：
        - 每次 iteration 输出 token 累计、耗时
        - 每次 tool call 输出名称、参数摘要、结果状态、耗时
        - Agent 结束时输出结构化 summary
        
        Args:
            user_prompt: The task prompt for the agent.
            max_iterations: Override the default MAX_ITERATIONS. If None, uses config.MAX_ITERATIONS.
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        run_log = AgentRunLog(agent_name=self.name)
        self._log.info(f"[{self.name}] Agent starting | prompt_len={len(user_prompt)} | state_mode={self.use_state}")
        start_time = time.time()
        AGENT_TIME_LIMIT_S = 3600

        usage: dict[str, int] = {"prompt": 0, "completion": 0}
        max_iter = max_iterations or config.MAX_ITERATIONS

        # 初始化 WorkspaceState（如果启用）
        if self.use_state:
            self._workspace_state = WorkspaceState.load(config.WORKSPACE)
            self._log.info(f"[{self.name}] WorkspaceState loaded: {self._workspace_state.total_files} files")

        for iteration in range(1, max_iter + 1):
            elapsed = time.time() - start_time
            if elapsed > AGENT_TIME_LIMIT_S:
                run_log.final_status = "timeout"
                run_log.error = f"exceeded {AGENT_TIME_LIMIT_S}s"
                run_log.emit()
                self._log.error(
                    f"[{self.name}] exceeded {AGENT_TIME_LIMIT_S}s time limit ({elapsed:.0f}s). Aborting."
                )
                return f"[error] Agent exceeded {AGENT_TIME_LIMIT_S}s time limit", usage

            # 上下文生命周期检查
            messages = self._check_context_lifecycle(messages)
            token_count = context.count_tokens(messages)

            self._log.info(
                f"[{self.name}] Iteration {iteration}/{config.MAX_ITERATIONS} | "
                f"elapsed: {elapsed:.0f}s | "
                f"tokens: {usage['prompt']}p + {usage['completion']}c | "
                f"context: {token_count}t"
            )

            # LLM 调用计时
            llm_start = time.time()
            try:
                response = client.chat.completions.create(
                    model=config.MODEL,
                    messages=messages,
                    tools=self.tools,
                    extra_body={"reasoning": {"type": "disabled"}},
                )
            except Exception as e:
                run_log.final_status = "llm_error"
                run_log.error = str(e)
                run_log.emit()
                self._log.error(f"[{self.name}] LLM call failed: {e}")
                return f"[error] LLM call failed: {e}", usage

            llm_latency = time.time() - llm_start

            # Accumulate token usage
            if response.usage:
                usage["prompt"] += response.usage.prompt_tokens or 0
                usage["completion"] += response.usage.completion_tokens or 0
                run_log.total_prompt_tokens = usage["prompt"]
                run_log.total_completion_tokens = usage["completion"]

            message = response.choices[0].message

            # 记录 LLM 回复摘要
            if message.content:
                content_preview = message.content[:200].replace("\n", " ")
                self._log.info(f"[{self.name}] Assistant ({llm_latency:.2f}s): {content_preview}...")

            # 记录 Tool Calls（关键！）
            if message.tool_calls:
                for tc in message.tool_calls:
                    fn = tc.function
                    args_preview = fn.arguments[:500]
                    self._log.info(
                        f"[{self.name}] Tool call: {fn.name} | "
                        f"args: {args_preview}{'...' if len(fn.arguments) > 500 else ''}"
                    )

            assistant_msg = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls] if message.tool_calls else []
            }
            reasoning_content = getattr(message, "reasoning_content", None)
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            messages.append(assistant_msg)

            if not message.tool_calls:
                run_log.final_status = "success"
                run_log.emit()
                # 尝试写入事件文件
                try:
                    run_log.write_jsonl(config.WORKSPACE)
                except Exception:
                    pass
                return message.content or "Done", usage

            # 执行工具
            for tool_call in message.tool_calls:
                name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)

                tool_start = time.time()
                result = execute_tool(name, arguments)
                if result is None:
                    result = "[error] Tool returned None"
                tool_latency = time.time() - tool_start
                tool_latency_ms = round(tool_latency * 1000)

                # 更新 WorkspaceState（如果启用）
                if self.use_state and self._workspace_state is not None:
                    self._workspace_state.update_from_tool_result(name, arguments, result)
                    self._workspace_state.save(config.WORKSPACE)

                # 结果摘要
                result_preview = result[:300].replace("\n", " ")
                status = "error" if result.startswith("[error]") else "ok"
                self._log.info(
                    f"[{self.name}] Tool result: {name} | "
                    f"status: {status} | "
                    f"latency: {tool_latency:.2f}s | "
                    f"length: {len(result)} | "
                    f"preview: {result_preview}{'...' if len(result) > 300 else ''}"
                )

                run_log.tool_calls.append({
                    "name": name,
                    "status": status,
                    "latency_ms": tool_latency_ms,
                    "result_len": len(result),
                })
                run_log.iterations.append({"iteration": iteration, "tool": name})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

        run_log.final_status = "max_iterations"
        run_log.emit()
        try:
            run_log.write_jsonl(config.WORKSPACE)
        except Exception:
            pass
        return f"[error] Max iterations reached ({max_iter}). Stopping to preserve budget.", usage

    def _check_context_lifecycle(self, messages: list[dict]) -> list[dict]:
        """检查并管理上下文生命周期（支持 WorkspaceState 分层）。"""
        token_count = context.count_tokens(messages)

        # 策略1: 极端情况 —— checkpoint + 重置
        if token_count > config.RESET_THRESHOLD or context.detect_anxiety(messages):
            self._log.warning(
                f"[{self.name}] Context reset triggered ({token_count} tokens) | "
                f"action: checkpoint + restore"
            )
            checkpoint = context.create_checkpoint(messages, self._llm_call_simple)
            return context.restore_from_checkpoint(checkpoint, self.system_prompt)

        # 策略2: 压缩历史
        elif token_count > config.COMPRESS_THRESHOLD:
            self._log.info(
                f"[{self.name}] Context compaction triggered ({token_count} tokens) | "
                f"threshold: {config.COMPRESS_THRESHOLD}"
            )
            return context.compact_messages(messages, self._llm_call_simple, self.name.lower())

        # 策略3: WorkspaceState 分层（P2）—— 用状态摘要替代工具返回
        elif self.use_state and self._workspace_state is not None and token_count > config.COMPRESS_THRESHOLD * 0.6:
            self._log.info(
                f"[{self.name}] State injection triggered ({token_count} tokens) | "
                f"replacing tool returns with state summary"
            )
            return inject_state_into_messages(
                messages, self._workspace_state, self.system_prompt
            )

        return messages

    def _llm_call_simple(self, messages: list[dict]) -> str:
        """简单的 LLM 调用（用于摘要）"""
        response = client.chat.completions.create(
            model=config.MODEL,
            messages=messages,
            temperature=0.3,
            extra_body={"reasoning": {"type": "disabled"}},
        )
        return response.choices[0].message.content or ""
