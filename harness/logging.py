"""
Harness 日志设置 + 统计输出
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

log = logging.getLogger("harness")


def setup_file_logging(workspace: Path, logger: logging.Logger) -> None:
    """为当前 workspace 设置独立的文件日志 Handler"""
    log_dir = workspace / "logs"
    log_dir.mkdir(exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"harness-{timestamp}.log"

    # 清理该 logger 已有的 Handler（避免恢复运行时重复）
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(file_handler)

    # 同时输出到控制台
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(console_handler)

    logger.info(f"[logging] File logging enabled: {log_path}")

    # 更新 harness.log 指向最新日志
    latest_link = workspace / "harness.log"
    try:
        latest_link.write_text(str(log_path), encoding="utf-8")
    except Exception:
        pass


def log_round_stats(
    logger: logging.Logger,
    round_num: int,
    score: float,
    sprint_score: float,
    overall_score: float,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_s: float,
) -> None:
    """输出单轮统计"""
    total = prompt_tokens + completion_tokens
    logger.info(
        f"[stats] Round {round_num:>2} | sprint {sprint_score:>4.1f} | overall {overall_score:>4.1f} | "
        f"tokens: {prompt_tokens:>6}p + {completion_tokens:>6}c = {total:>7} | "
        f"elapsed: {elapsed_s:>6.1f}s"
    )


def log_final_stats(
    logger: logging.Logger,
    round_stats: list[dict],
    sprint_score_history: list[float],
    overall_score_history: list[float],
    token_totals: dict[str, int],
) -> None:
    """输出最终统计表格"""
    if not round_stats:
        return

    logger.info("\n" + "="*72)
    logger.info("Cost / Time Summary")
    logger.info("="*72)
    logger.info(
        f"{'Round':>5} | {'Sprint':>6} | {'Overall':>7} | {'Strategy':>8} | "
        f"{'Prompt':>7} | {'Compl.':>7} | {'Total':>7} | {'Time(s)':>7}"
    )
    logger.info("-"*80)
    for i, s in enumerate(round_stats):
        total = s["prompt_tokens"] + s["completion_tokens"]
        sprint_s = sprint_score_history[i] if i < len(sprint_score_history) else 0.0
        overall_s = overall_score_history[i] if i < len(overall_score_history) else 0.0
        logger.info(
            f"{s['round']:>5} | {sprint_s:>6.1f} | {overall_s:>7.1f} | {s['strategy']:>8} | "
            f"{s['prompt_tokens']:>7} | {s['completion_tokens']:>7} | "
            f"{total:>7} | {s['elapsed_s']:>7.1f}"
        )
    logger.info("-"*72)
    grand_total = token_totals["prompt"] + token_totals["completion"]
    total_time = sum(s["elapsed_s"] for s in round_stats)
    logger.info(
        f"{'TOTAL':>5} | {'':>5} | {'':>8} | "
        f"{token_totals['prompt']:>7} | {token_totals['completion']:>7} | "
        f"{grand_total:>7} | {total_time:>7.1f}"
    )
    logger.info("="*72)
