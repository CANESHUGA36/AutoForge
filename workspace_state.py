"""
WorkspaceState — 上下文分层管理（P2）

核心思想：用结构化状态替代 messages 中的工具返回内容，
让 Agent 的上下文增长从 O(代码量) 降到 O(操作次数)。

状态分层：
- L0: 系统提示 + 当前任务（始终保留）
- L1: WorkspaceState 摘要（动态更新）
- L2: 最近 N 轮对话（保留原始 messages）
- L3: 更早历史的压缩摘要（LLM 生成）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("harness")


@dataclass
class FileState:
    """单个文件的跟踪状态"""
    path: str
    size: int = 0
    lines: int = 0
    last_modified: float = 0.0
    summary: str = ""  # 内容摘要（前 200 字）

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "lines": self.lines,
            "summary": self.summary[:100],
        }


@dataclass
class WorkspaceState:
    """Workspace 的共享状态，替代部分 messages 的功能。

    设计原则：
    1. 可序列化（JSON）—— 支持持久化和监控
    2. 可摘要（summarize）—— 生成给 LLM 的精简描述
    3. 增量更新（update）—— 基于工具调用结果更新，不重复扫描
    """
    # 文件系统状态
    files: dict[str, FileState] = field(default_factory=dict)
    total_files: int = 0
    total_lines: int = 0

    # Git 状态
    commits: list[str] = field(default_factory=list)
    uncommitted_changes: bool = False

    # 构建/测试状态
    last_build_status: str = "unknown"  # ok | error | not_run
    last_build_errors: list[str] = field(default_factory=list)
    last_test_status: str = "unknown"
    test_coverage: float = 0.0

    # 当前 Sprint
    current_sprint_goal: str = ""
    current_sprint_tasks: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)

    # 错误/问题跟踪
    open_issues: list[str] = field(default_factory=list)
    resolved_issues: list[str] = field(default_factory=list)

    # 依赖状态
    dependencies_installed: bool = False
    dev_server_running: bool = False
    dev_server_port: int = 0

    def update_from_tool_result(self, tool_name: str, arguments: dict, result: str) -> None:
        """基于工具调用结果增量更新状态。"""
        if tool_name == "write_file":
            self._update_file(arguments.get("path", ""), result)
        elif tool_name == "edit_file":
            self._update_file(arguments.get("path", ""), result)
        elif tool_name == "run_bash":
            self._update_from_bash(arguments.get("command", ""), result)
        elif tool_name == "browser_test":
            self._update_from_browser(result)
        elif tool_name == "list_files":
            self._update_file_list(result)

    def _update_file(self, path: str, result: str) -> None:
        """更新文件状态"""
        if not path or result.startswith("[error]"):
            return
        try:
            p = Path(config.WORKSPACE, path).resolve()
            if not p.exists():
                return
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.count("\n") + 1
            self.files[path] = FileState(
                path=path,
                size=len(content),
                lines=lines,
                last_modified=p.stat().st_mtime,
                summary=content[:200].replace("\n", " "),
            )
            self.total_files = len(self.files)
            self.total_lines = sum(f.lines for f in self.files.values())
        except Exception as e:
            log.debug(f"[state] Failed to update file state for {path}: {e}")

    def _update_from_bash(self, command: str, result: str) -> None:
        """从 bash 结果推断状态"""
        cmd_lower = command.lower()

        # 检测依赖安装
        if any(kw in cmd_lower for kw in ["npm install", "pip install", "yarn"]):
            if not result.startswith("[error]") and "[exit code:" not in result:
                self.dependencies_installed = True
            else:
                self.last_build_status = "error"
                self.last_build_errors.append(f"deps: {result[:200]}")

        # 检测构建
        if any(kw in cmd_lower for kw in ["npm run build", "vite build", "tsc"]):
            if result.startswith("[error]") or "[exit code:" in result:
                self.last_build_status = "error"
                self.last_build_errors.append(result[:300])
            else:
                self.last_build_status = "ok"
                self.last_build_errors = []

        # 检测 dev server
        if any(kw in cmd_lower for kw in ["npm run dev", "npx serve", "python -m http.server"]):
            self.dev_server_running = True
            # 简单端口提取
            import re
            port_match = re.search(r'-l\s+(\d+)|:(\d+)|--port\s+(\d+)', command)
            if port_match:
                self.dev_server_port = int(next(g for g in port_match.groups() if g))

        # 检测 git commit
        if cmd_lower.startswith("git commit"):
            if not result.startswith("[error]"):
                msg = command.split("-m", 1)[-1].strip().strip('"\'') if "-m" in command else "commit"
                self.commits.append(msg)
                self.uncommitted_changes = False

        # 检测 git status
        if cmd_lower.startswith("git status"):
            self.uncommitted_changes = len(result.strip()) > 0

    def _update_from_browser(self, result: str) -> None:
        """从浏览器测试结果推断状态"""
        if "[error]" in result:
            self.last_test_status = "error"
        elif "Navigation failed" in result:
            self.last_test_status = "error"
        else:
            self.last_test_status = "ok"

    def _update_file_list(self, result: str) -> None:
        """从 list_files 结果更新文件列表"""
        if result.startswith("[error]") or result == "(empty)":
            return
        known_paths = set(self.files.keys())
        current_paths = set()
        for line in result.splitlines()[:200]:
            line = line.strip()
            # list_files 输出格式: "F  path/to/file" 或 "D  path/to/dir"
            if line and len(line) > 3 and line[1:3] == "  ":
                current_paths.add(line[3:])
        # 检测删除的文件
        for removed in known_paths - current_paths:
            self.files.pop(removed, None)
        self.total_files = len(self.files)

    def summarize(self, max_chars: int = 2000) -> str:
        """生成给 LLM 的状态摘要。

        这是核心方法：用几百字的结构化摘要替代几千字的工具返回历史。
        """
        parts = ["## Workspace State"]

        # 文件概览
        parts.append(f"\n### Files ({self.total_files} files, {self.total_lines} lines)")
        # 只列出最近修改的 10 个文件
        recent_files = sorted(
            self.files.values(),
            key=lambda f: f.last_modified,
            reverse=True
        )[:10]
        for f in recent_files:
            parts.append(f"- {f.path} ({f.lines}L)")

        # 构建状态
        parts.append(f"\n### Build Status: {self.last_build_status}")
        if self.last_build_errors:
            parts.append(f"Recent errors: {self.last_build_errors[-1][:150]}")

        # 测试状态
        parts.append(f"\n### Test Status: {self.last_test_status}")

        # Sprint 状态
        if self.current_sprint_goal:
            parts.append(f"\n### Current Sprint: {self.current_sprint_goal}")
            if self.completed_tasks:
                parts.append(f"Completed: {len(self.completed_tasks)}/{len(self.current_sprint_tasks) + len(self.completed_tasks)}")

        # 未解决问题
        if self.open_issues:
            parts.append(f"\n### Open Issues ({len(self.open_issues)})")
            for issue in self.open_issues[:5]:
                parts.append(f"- {issue[:100]}")

        summary = "\n".join(parts)
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "\n...[TRUNCATED]"
        return summary

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "files": {k: v.to_dict() for k, v in self.files.items()},
            "commits": self.commits[-10:],  # 只保留最近 10 个
            "uncommitted_changes": self.uncommitted_changes,
            "last_build_status": self.last_build_status,
            "last_test_status": self.last_test_status,
            "current_sprint_goal": self.current_sprint_goal,
            "completed_tasks": self.completed_tasks,
            "open_issues": self.open_issues,
        }

    def save(self, workspace: str) -> None:
        """保存到 workspace 目录"""
        path = Path(workspace) / ".workspace_state.json"
        try:
            path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.debug(f"[state] Failed to save workspace state: {e}")

    @classmethod
    def load(cls, workspace: str) -> "WorkspaceState":
        """从 workspace 目录加载"""
        path = Path(workspace) / ".workspace_state.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = cls()
            state.total_files = data.get("total_files", 0)
            state.total_lines = data.get("total_lines", 0)
            state.last_build_status = data.get("last_build_status", "unknown")
            state.last_test_status = data.get("last_test_status", "unknown")
            state.current_sprint_goal = data.get("current_sprint_goal", "")
            state.completed_tasks = data.get("completed_tasks", [])
            state.open_issues = data.get("open_issues", [])
            # score_history removed — scores are tracked in harness_state.json only
            return state
        except Exception as e:
            log.warning(f"[state] Failed to load workspace state: {e}")
            return cls()


def inject_state_into_messages(
    messages: list[dict],
    state: WorkspaceState,
    system_prompt: str,
) -> list[dict]:
    """将 WorkspaceState 摘要注入 messages，替代过长的工具返回。

    策略：
    1. 保留 system prompt
    2. 保留最近的用户输入
    3. 将中间的工具返回替换为 state 摘要
    4. 保留最后的 assistant 消息（如果有）
    """
    if not messages:
        return messages

    # 找到 system message
    system_idx = 0 if messages[0].get("role") == "system" else -1
    system_msg = messages[0] if system_idx == 0 else {"role": "system", "content": system_prompt}

    # 保留最近 4 轮对话（assistant + tool 算一轮）
    recent_messages = messages[-8:] if len(messages) > 8 else messages[1:]

    # 构建新 messages
    new_messages = [system_msg]

    # 注入 state 摘要
    state_summary = state.summarize(max_chars=1500)
    new_messages.append({
        "role": "user",
        "content": f"[WORKSPACE STATE SUMMARY]\n{state_summary}\n\nContinue from above.",
    })

    # 追加最近对话
    new_messages.extend(recent_messages)

    return new_messages
