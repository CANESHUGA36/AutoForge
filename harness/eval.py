"""
评分解析 — 纯函数，无状态，易于测试
"""
from __future__ import annotations

import logging
import re

import config

log = logging.getLogger("harness")


def parse_scores(text: str) -> tuple[float, float]:
    """Parse SPRINT_SCORE and OVERALL_SCORE from evaluator feedback.

    Returns (sprint_score, overall_score).
    If new format not found, falls back to legacy SCORE line.
    """
    sprint_match = re.search(r'SPRINT_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
    overall_match = re.search(r'OVERALL_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)

    if sprint_match and overall_match:
        return float(sprint_match.group(1)), float(overall_match.group(1))

    # Fallback: legacy single SCORE line
    legacy_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
    if legacy_match:
        score = float(legacy_match.group(1))
        log.warning("Legacy single SCORE found — using it for both sprint and overall")
        return score, score

    log.warning("Could not parse any score, defaulting to 0")
    return 0.0, 0.0


def parse_dimension_scores(text: str) -> dict:
    """Parse per-dimension scores from '### Dimension Name: X/10' headings."""
    _name_map = {
        "design quality": "design_quality",
        "design_quality": "design_quality",
        "originality":    "originality",
        "craft":          "craft",
        "functionality":  "functionality",
    }
    scores: dict = {}
    pattern = r'###\s*([\w\s]+?):\s*(\d+(?:\.\d+)?)\s*/\s*10'
    for match in re.finditer(pattern, text, re.IGNORECASE):
        raw = match.group(1).strip().lower()
        key = _name_map.get(raw)
        if key:
            scores[key] = float(match.group(2))
    return scores


def check_dimension_thresholds(dim_scores: dict) -> list[str]:
    """Return list of failure strings for dimensions below hard thresholds."""
    failed = []
    for dim, threshold in config.DIMENSION_THRESHOLDS.items():
        score = dim_scores.get(dim)
        if score is not None and score < threshold:
            failed.append(f"{dim}={score:.1f} (threshold {threshold:.1f})")
    return failed
