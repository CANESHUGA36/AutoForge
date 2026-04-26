"""
Evaluator Result Cache — 评估结果缓存与摘要

核心思想：将每轮的 Reviewer 完整报告保存到磁盘，
Judge 直接读 Reviewer 原始报告，不依赖 EvalCache 做预处理。

缓存文件：
- .eval_cache/round_{N}_review.md — 完整 Reviewer 审查报告
"""
from __future__ import annotations

from pathlib import Path


class EvalCache:
    """评估结果缓存管理器（简化版）"""

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.cache_dir = self.workspace / ".eval_cache"
        self.cache_dir.mkdir(exist_ok=True)

    def save_round(self, round_num: int, review_result: str) -> None:
        """保存 Reviewer 统一报告"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        review_path = self.cache_dir / f"round_{round_num}_review.md"
        review_path.write_text(review_result, encoding="utf-8")

    def get_full_report(self, round_num: int) -> str | None:
        """获取完整 Reviewer 报告"""
        path = self.cache_dir / f"round_{round_num}_review.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None
