"""
功能组（Feature Group）管理 — 按 contract.md 中的功能编号拆分阶段

将 contract 的 142+ 项标准按 F1/F2/.../F17 + D + T 拆分为功能组，
每轮 Sprint 只推进一个功能组，Reviewer 只验证当前组。
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger("harness")

# --------------------------------------------------------------------------- #
#  Tier 定义
# --------------------------------------------------------------------------- #
TIER_1_GROUPS = ["F1", "F2", "F3", "F4"]
TIER_2_GROUPS = ["F5", "F6", "F7", "F8", "F9"]
TIER_3_GROUPS = ["F10", "F11", "F12", "F13", "F14", "F15", "F16", "F17"]
DESIGN_PREFIX = "D"
TECHNICAL_PREFIX = "T"

TIER_REQUIREMENTS: dict[str, dict] = {
    "tier1": {"groups": TIER_1_GROUPS, "min_rate": 1.0, "label": "MVP Core"},
    "tier2": {"groups": TIER_2_GROUPS, "min_rate": 0.80, "label": "Core Experience"},
}

OVERALL_PASS_THRESHOLD = 0.75
GROUP_PASS_THRESHOLD_DEFAULT = 0.70

# 连续卡死检测
MAX_STUCK_ROUNDS = 3


# --------------------------------------------------------------------------- #
#  从 contract.md 解析功能组
# --------------------------------------------------------------------------- #

_GROUP_HEADER_RE = re.compile(
    r"^#{2,4}\s+(F\d+|D\d*|T\d*)[\s:：.]+(.+)$",
    re.MULTILINE,
)

_CRITERIA_RE = re.compile(
    r'^\s*-\s+\[[^\]]*\]\s+\*\*(?P<id>[A-Z]\d+(?:\.\d+)?)\*\*',
    re.MULTILINE,
)

# Table-format: | **F1** | Feature Name | ... |
_TABLE_GROUP_RE = re.compile(
    r"\|\s*\*\*(F\d+|D\d*|T\d*)\*\*\s*\|\s*([^|]+)",
)


def parse_feature_groups(contract_text: str) -> list[dict]:
    """Parse contract.md into feature groups.

    Supports two formats:
    1. Heading format: ### F1 Name  +  - [ ] **F1.1** ...
    2. Table format:  | **F1** | Name | ... | (Architect-generated)

    Returns list of {"id": "F1", "name": "Audio File Upload System", "criteria": ["F1.1", ...]}
    """
    groups: list[dict] = []

    # --- Attempt 1: Heading + list format ---
    lines = contract_text.splitlines()
    for line in lines:
        header_match = _GROUP_HEADER_RE.match(line.strip())
        if header_match:
            gid, name = header_match.groups()
            groups.append({
                "id": gid,
                "name": name.strip(),
                "criteria": [],
            })

    for m in _CRITERIA_RE.finditer(contract_text):
        cid = m.group("id")
        # Assign to nearest preceding group whose ID is a prefix of this criterion
        # e.g., F1.1 -> F1, F10.1 -> F10, D1 -> D
        for g in reversed(groups):
            if cid.startswith(g["id"] + ".") or cid == g["id"]:
                g["criteria"].append(cid)
                break

    if groups:
        for g in groups:
            g["criteria"] = sorted(set(g["criteria"]), key=_criteria_sort_key)
        total = sum(len(g["criteria"]) for g in groups)
        log.info(f"[feature_groups] Parsed {len(groups)} groups, {total} criteria total")
        return groups

    # --- Attempt 2: Table format fallback ---
    log.warning("[feature_groups] No heading-format groups found, trying table format")
    table_groups: dict[str, dict] = {}
    for line in lines:
        # Skip header separator lines
        if re.match(r"^\|[-\s|]+$", line.strip()):
            continue
        m = _TABLE_GROUP_RE.search(line)
        if m:
            gid, name = m.groups()
            gid = gid.strip()
            name = name.strip()
            if gid not in table_groups:
                table_groups[gid] = {
                    "id": gid,
                    "name": name,
                    "criteria": [],
                }
            # Assign a virtual criterion number (F1.1, F1.2, ...)
            n = len(table_groups[gid]["criteria"]) + 1
            table_groups[gid]["criteria"].append(f"{gid}.{n}")

    groups = list(table_groups.values())
    for g in groups:
        g["criteria"] = sorted(set(g["criteria"]), key=_criteria_sort_key)

    total = sum(len(g["criteria"]) for g in groups)
    log.info(f"[feature_groups] Table format: {len(groups)} groups, {total} criteria total")
    for g in groups:
        log.debug(f"  {g['id']}: {g['name']} ({len(g['criteria'])} criteria)")

    return groups


def _criteria_sort_key(cid: str) -> tuple:
    """Sort criteria like F1.1, F1.2, F10.1."""
    import re as _re
    m = _re.match(r"([A-Z])(\d+)(?:\.(\d+))?", cid)
    if not m:
        return (cid, 0, 0)
    letter = m.group(1)
    major = int(m.group(2))
    minor = int(m.group(3) or 0)
    return (letter, major, minor)


# --------------------------------------------------------------------------- #
#  功能组状态机
# --------------------------------------------------------------------------- #

class FeatureGroupState:
    """维护功能组的推进状态。"""

    def __init__(self, groups: list[dict]):
        self.groups = groups  # [{"id": "F1", "name": "...", "criteria": [...]}, ...]
        self.group_ids = [g["id"] for g in groups]
        self.group_map = {g["id"]: g for g in groups}

        # 每轮结束后更新：group_id -> 最新通过率 (0.0-1.0)
        self.pass_rates: dict[str, float] = {}

        # 每轮结束后更新：group_id -> 连续未通过轮次
        self.stuck_counts: dict[str, int] = {}

        # 当前指针（group_ids 中的索引）
        self.current_idx = 0

    @property
    def current_group(self) -> dict | None:
        if 0 <= self.current_idx < len(self.group_ids):
            return self.group_map[self.group_ids[self.current_idx]]
        return None

    @property
    def current_group_id(self) -> str:
        g = self.current_group
        return g["id"] if g else ""

    def advance(self) -> bool:
        """推进到下一个功能组。返回是否成功推进。"""
        if self.current_idx < len(self.group_ids) - 1:
            self.current_idx += 1
            log.info(
                f"[feature_groups] Advanced to {self.current_group_id} "
                f"({self.current_group['name']})"
            )
            return True
        log.info("[feature_groups] All groups completed")
        return False

    def update_rate(self, group_id: str, rate: float) -> None:
        """更新某功能组的最新通过率。"""
        old_rate = self.pass_rates.get(group_id, 0.0)
        self.pass_rates[group_id] = rate

        threshold = _get_group_threshold(group_id)
        if rate >= threshold:
            self.stuck_counts[group_id] = 0
            log.info(
                f"[feature_groups] {group_id} passed at {rate:.0%} "
                f"(threshold {threshold:.0%})"
            )
        else:
            self.stuck_counts[group_id] = self.stuck_counts.get(group_id, 0) + 1
            log.info(
                f"[feature_groups] {group_id} not yet passed: {rate:.0%} "
                f"(threshold {threshold:.0%}, stuck={self.stuck_counts[group_id]})"
            )

    def check_should_advance(self) -> bool:
        """检查当前功能组是否达到通过阈值，可以推进。"""
        gid = self.current_group_id
        if not gid:
            return False
        rate = self.pass_rates.get(gid, 0.0)
        threshold = _get_group_threshold(gid)
        return rate >= threshold

    def is_complete(self) -> bool:
        """检查是否所有功能组都已完成。"""
        for gid in self.group_ids:
            threshold = _get_group_threshold(gid)
            if self.pass_rates.get(gid, 0.0) < threshold:
                return False
        return True

    def tier_status(self) -> dict:
        """返回各 Tier 的完成状态。"""
        def _tier_rate(groups: list[str]) -> float:
            total = sum(len(self.group_map[g]["criteria"]) for g in groups if g in self.group_map)
            passed = sum(
                len(self.group_map[g]["criteria"]) * self.pass_rates.get(g, 0.0)
                for g in groups if g in self.group_map
            )
            return passed / total if total > 0 else 0.0

        def _tier_groups_complete(groups: list[str], threshold: float) -> tuple[int, int]:
            complete = sum(
                1 for g in groups
                if g in self.group_map and self.pass_rates.get(g, 0.0) >= threshold
            )
            return complete, len(groups)

        result = {}
        for tier_name, cfg in TIER_REQUIREMENTS.items():
            grp = cfg["groups"]
            comp, total = _tier_groups_complete(grp, cfg["min_rate"])
            result[tier_name] = {
                "rate": _tier_rate(grp),
                "groups_complete": comp,
                "groups_total": total,
                "min_rate": cfg["min_rate"],
                "passed": comp == total,
            }
        return result

    def overall_rate(self) -> float:
        """计算全局 overall 通过率。"""
        total_criteria = sum(len(g["criteria"]) for g in self.groups)
        passed_criteria = sum(
            len(g["criteria"]) * self.pass_rates.get(g["id"], 0.0)
            for g in self.groups
        )
        return passed_criteria / total_criteria if total_criteria > 0 else 0.0

    def any_group_stuck(self) -> tuple[bool, str | None]:
        """检查是否有功能组连续卡死。"""
        for gid, count in self.stuck_counts.items():
            if count >= MAX_STUCK_ROUNDS:
                return True, gid
        return False, None

    def to_dict(self) -> dict:
        return {
            "current_idx": self.current_idx,
            "current_group": self.current_group_id,
            "pass_rates": dict(self.pass_rates),
            "stuck_counts": dict(self.stuck_counts),
            "overall": self.overall_rate(),
            "tier_status": self.tier_status(),
        }


def _get_group_threshold(group_id: str) -> float:
    """获取功能组的通过阈值。"""
    for tier_name, cfg in TIER_REQUIREMENTS.items():
        if group_id in cfg["groups"]:
            return cfg["min_rate"]
    return GROUP_PASS_THRESHOLD_DEFAULT


def get_group_instruction(group_id: str, group: dict) -> str:
    """生成功能组的中文说明，注入 Builder prompt。"""
    lines = [
        f"## 当前 Sprint 目标：{group_id} — {group['name']}",
        f"",
        f"本轮你只需实现并完善 **{group_id}** 的功能。",
        f"验收标准：contract.md 中 {group_id}.1 ~ {group_id}.{len(group['criteria'])} 共 {len(group['criteria'])} 项。",
        f"",
        f"**重要规则**：",
        f"- 不要实现其他功能组（如 F5、F9）的内容",
        f"- 如果 {group_id} 的代码已部分存在，检查并修复它",
        f"- 完成后用 git commit 提交",
    ]
    return "\n".join(lines)
