"""
策略解析 — 从 Builder 输出中提取策略声明
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("harness")


def parse_strategy(text: str) -> dict:
    """Extract STRATEGY / REASON / NEW DIRECTION from Builder output.

    Returns dict with keys: strategy ("REFINE"|"PIVOT"|"UNKNOWN"),
    reason (str), new_direction (str|None).
    """
    result = {"strategy": "UNKNOWN", "reason": "", "new_direction": None}

    strategy_match = re.search(r'STRATEGY:\s*(REFINE|PIVOT)', text, re.IGNORECASE)
    if strategy_match:
        result["strategy"] = strategy_match.group(1).upper()

    reason_match = re.search(r'REASON:\s*(.+)', text)
    if reason_match:
        result["reason"] = reason_match.group(1).strip()

    direction_match = re.search(r'NEW DIRECTION:\s*(.+?)(?:\n---|\Z)', text, re.DOTALL)
    if direction_match:
        result["new_direction"] = direction_match.group(1).strip()

    if result["strategy"] == "UNKNOWN":
        log.warning("Builder did not include a STRATEGY declaration — defaulting to REFINE")
        result["strategy"] = "REFINE"

    return result
