"""
Git 操作封装 — 隐藏 git 命令细节
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import config

log = logging.getLogger("harness")


class GitManager:
    """封装所有 Git 操作"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def init_repo(self) -> None:
        """初始化 git 仓库（如果不存在）"""
        git_dir = self.workspace / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "harness@example.com"],
                cwd=self.workspace, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Harness"],
                cwd=self.workspace, capture_output=True,
            )

    def get_head_hash(self) -> str | None:
        """获取当前 HEAD 的 commit hash"""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def get_commit_for_round(self, round_num: int) -> str | None:
        """从 git log 动态查找某轮的 commit hash"""
        result = subprocess.run(
            ["git", "log", "--format=%H", "--grep", f"round {round_num} snapshot"],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
        return None

    def commit_round(self, round_num: int) -> str | None:
        """每轮 Build 结束后强制做一次 git 快照"""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        if not result.stdout.strip():
            log.info(f"[git] Nothing to commit after round {round_num}")
        else:
            subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
            msg = f"harness: round {round_num} snapshot"
            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.workspace, capture_output=True, text=True,
                **config.SUBPROCESS_TEXT_KWARGS,
            )
            if commit.returncode == 0:
                log.info(f"[git] Committed round {round_num} snapshot")
            else:
                log.warning(f"[git] Commit failed: {commit.stderr.strip()}")
        return self.get_head_hash()

    def rollback_to(self, commit_hash: str, reason: str) -> None:
        """将 workspace 硬重置到指定 commit"""
        log.info(f"[git] Rolling back to {commit_hash[:8]} — {reason}")
        result = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            cwd=self.workspace, capture_output=True, text=True,
            **config.SUBPROCESS_TEXT_KWARGS,
        )
        if result.returncode == 0:
            log.info("[git] Rollback successful")
        else:
            log.warning(f"[git] Rollback failed: {result.stderr.strip()}")
