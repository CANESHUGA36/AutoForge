"""
Harness 状态管理 — 持久化/恢复中断状态
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger("harness")


class StateManager:
    """管理 harness_state.json 的原子读写"""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def _state_path(self) -> Path:
        return self.workspace / config.STATE_FILE

    def save(self, state: dict) -> None:
        """原子写入状态文件"""
        tmp = self._state_path().with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(self._state_path())
            log.debug(f"[state] Saved to {config.STATE_FILE}")
        except Exception as e:
            log.warning(f"[state] Failed to save state: {e}")

    def load(self) -> dict | None:
        """加载状态，返回字典或 None"""
        path = self._state_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[state] Could not load state: {e}")
            return None

    def clear(self) -> None:
        """删除状态文件"""
        path = self._state_path()
        try:
            if path.exists():
                path.unlink()
                log.debug(f"[state] Cleared {config.STATE_FILE}")
        except Exception as e:
            log.warning(f"[state] Could not remove state file: {e}")
