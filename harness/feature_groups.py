"""
功能组（Feature Group）管理 — 按 contract.md 中的大组（Group）拆分阶段

将 contract 的标准按大组（Group 1, Group 2, ...）拆分，
每个大组包含多个子功能，每轮 Sprint 只推进一个大组。
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger("harness")

# --------------------------------------------------------------------------- #
#  Tier 定义
# --------------------------------------------------------------------------- #
# 大组模式下，tier 概念简化：
# - 所有大组都是功能性的，没有单独的 D/T 组
# - 通过阈值统一使用 GROUP_PASS_THRESHOLD

GROUP_PASS_THRESHOLD_DEFAULT = 0.70  # 默认大组通过阈值
OVERALL_PASS_THRESHOLD = 0.75  # 全局通过阈值
MAX_STUCK_ROUNDS = 3  # 连续卡死检测


# --------------------------------------------------------------------------- #
#  从 contract.md 解析大组
# --------------------------------------------------------------------------- #

# 大组标题: ## Group 1: Core Canvas
_GROUP_HEADER_RE = re.compile(
    r"^#{2}\s+Group\s+(\d+)[:：]\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# 子功能标题: ### Infinite Canvas with Pan/Zoom
_SUB_FEATURE_RE = re.compile(
    r"^#{3}\s+(.+)$",
    re.MULTILINE,
)

# 标准项: - [ ] **G1.A.1** 测试场景 — 期望结果
# 格式: G{group_num}.{sub_feature_letter}.{seq}
_CRITERIA_RE = re.compile(
    r'^\s*-\s+\[[^\]]*\]\s+\*\*G(?P<group>\d+)\.(?P<sub>[A-Z])\.(?P<seq>\d+)\*\*',
    re.MULTILINE,
)

# 兼容旧格式: F1.1, F2.3 等（用于向后兼容）
_LEGACY_CRITERIA_RE = re.compile(
    r'^\s*-\s+\[[^\]]*\]\s+\*\*(?P<id>[A-Z]\d+(?:\.\d+)?)\*\*',
    re.MULTILINE,
)


def parse_feature_groups(contract_text: str) -> list[dict]:
    """Parse contract.md into feature groups (大组模式).

    新格式:
        ## Group 1: Core Canvas
        ### Infinite Canvas
        - [ ] **G1.A.1** Middle mouse button drag pans...
        - [ ] **G1.A.2** Space + left mouse drag...
        ### Shape Drawing
        - [ ] **G1.B.1** Rectangle tool...

    兼容旧格式:
        ### F1: Infinite Canvas
        - [ ] **F1.1** Middle mouse button drag...

    Returns list of {"id": "G1", "name": "Core Canvas", "criteria": ["G1.A.1", ...], "sub_features": [{"name": "...", "criteria": [...]}]}
    """
    groups: list[dict] = []
    lines = contract_text.splitlines()

    # --- Attempt 1: 大组格式 (Group N: Name) ---
    current_group = None
    current_sub = None

    for line in lines:
        # 检测大组标题
        group_match = _GROUP_HEADER_RE.match(line.strip())
        if group_match:
            group_num, name = group_match.groups()
            current_group = {
                "id": f"G{group_num}",
                "name": name.strip(),
                "criteria": [],
                "sub_features": [],
            }
            groups.append(current_group)
            current_sub = None
            continue

        # 检测子功能标题
        if current_group:
            sub_match = _SUB_FEATURE_RE.match(line.strip())
            if sub_match:
                sub_name = sub_match.group(1).strip()
                current_sub = {
                    "name": sub_name,
                    "criteria": [],
                }
                current_group["sub_features"].append(current_sub)
                continue

    # 提取标准项并分配到对应大组
    for m in _CRITERIA_RE.finditer(contract_text):
        group_num = m.group("group")
        sub_letter = m.group("sub")
        seq = m.group("seq")
        cid = f"G{group_num}.{sub_letter}.{seq}"

        # 找到对应的大组
        target_group = None
        for g in groups:
            if g["id"] == f"G{group_num}":
                target_group = g
                break

        if target_group:
            target_group["criteria"].append(cid)
            # 也分配到子功能
            sub_idx = ord(sub_letter) - ord('A')
            if 0 <= sub_idx < len(target_group.get("sub_features", [])):
                target_group["sub_features"][sub_idx]["criteria"].append(cid)

    if groups:
        for g in groups:
            g["criteria"] = sorted(set(g["criteria"]), key=_criteria_sort_key)
            for sub in g.get("sub_features", []):
                sub["criteria"] = sorted(set(sub["criteria"]), key=_criteria_sort_key)
        total = sum(len(g["criteria"]) for g in groups)
        log.info(f"[feature_groups] Parsed {len(groups)} groups, {total} criteria total (新格式)")
        for g in groups:
            log.info(f"  {g['id']}: {g['name']} ({len(g['criteria'])} criteria, {len(g.get('sub_features', []))} sub-features)")
        return groups

    # --- Attempt 2: 兼容旧格式 (F1, F2, ...) ---
    log.warning("[feature_groups] No Group format found, trying legacy F-format")
    return _parse_legacy_groups(contract_text)


def _parse_legacy_groups(contract_text: str) -> list[dict]:
    """解析旧格式的 F-group。"""
    legacy_group_re = re.compile(
        r"^#{2,4}\s+(F\d+|D\d*|T\d*)[\s:：.]+(.+)$",
        re.MULTILINE,
    )
    groups: list[dict] = []

    for line in contract_text.splitlines():
        m = legacy_group_re.match(line.strip())
        if m:
            gid, name = m.groups()
            groups.append({
                "id": gid,
                "name": name.strip(),
                "criteria": [],
                "sub_features": [],
            })

    for m in _LEGACY_CRITERIA_RE.finditer(contract_text):
        cid = m.group("id")
        for g in reversed(groups):
            if cid.startswith(g["id"] + ".") or cid == g["id"]:
                g["criteria"].append(cid)
                break

    for g in groups:
        g["criteria"] = sorted(set(g["criteria"]), key=_criteria_sort_key)

    total = sum(len(g["criteria"]) for g in groups)
    log.info(f"[feature_groups] Legacy format: {len(groups)} groups, {total} criteria total")
    return groups


def _criteria_sort_key(cid: str) -> tuple:
    """Sort criteria like G1.A.1, G1.B.3, G2.C.1."""
    import re as _re
    # 新格式: G1.A.1
    m = _re.match(r"G(\d+)\.([A-Z])\.(\d+)", cid)
    if m:
        return (0, int(m.group(1)), m.group(2), int(m.group(3)))
    # 旧格式: F1.1
    m = _re.match(r"([A-Z])(\d+)(?:\.(\d+))?", cid)
    if m:
        return (1, m.group(1), int(m.group(2)), int(m.group(3) or 0))
    return (999, cid, 0, 0)


# --------------------------------------------------------------------------- #
#  功能组状态机
# --------------------------------------------------------------------------- #

class FeatureGroupState:
    """维护大组的推进状态。"""

    def __init__(self, groups: list[dict]):
        self.groups = groups  # [{"id": "G1", "name": "...", "criteria": [...], "sub_features": [...]}, ...]
        self.group_ids = [g["id"] for g in groups]
        self.group_map = {g["id"]: g for g in groups}

        # 每轮结束后更新：group_id -> 最新通过率 (0.0-1.0)
        self.pass_rates: dict[str, float] = {}

        # 每轮结束后更新：group_id -> 连续未通过轮次
        self.stuck_counts: dict[str, int] = {}

        # 当前指针（group_ids 中的索引）
        self.current_idx = 0

        # CRITICAL_BUG 追踪：group_id -> bool
        self.critical_bugs: dict[str, bool] = {}

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
        """推进到下一个大组。返回是否成功推进。"""
        if self.current_idx < len(self.group_ids) - 1:
            self.current_idx += 1
            log.info(
                f"[feature_groups] Advanced to {self.current_group_id} "
                f"({self.current_group['name']})"
            )
            return True
        log.info("[feature_groups] All groups completed")
        return False

    def update_rate(self, group_id: str, rate: float, has_critical_bug: bool = False) -> None:
        """更新某大组的最新通过率和 CRITICAL_BUG 状态。"""
        old_rate = self.pass_rates.get(group_id, 0.0)
        self.pass_rates[group_id] = rate
        self.critical_bugs[group_id] = has_critical_bug

        threshold = GROUP_PASS_THRESHOLD_DEFAULT
        passed = rate >= threshold and not has_critical_bug

        if passed:
            self.stuck_counts[group_id] = 0
            log.info(
                f"[feature_groups] {group_id} passed at {rate:.0%} "
                f"(threshold {threshold:.0%}, no critical bug)"
            )
        else:
            self.stuck_counts[group_id] = self.stuck_counts.get(group_id, 0) + 1
            reason = "below threshold" if rate < threshold else "critical bug"
            log.info(
                f"[feature_groups] {group_id} not yet passed: {rate:.0%} "
                f"(threshold {threshold:.0%}, {reason}, stuck={self.stuck_counts[group_id]})"
            )

    def check_should_advance(self) -> bool:
        """检查当前大组是否达到通过条件，可以推进。

        通过条件：
        1. 通过率 >= 阈值
        2. 无 CRITICAL_BUG
        """
        gid = self.current_group_id
        if not gid:
            return False
        rate = self.pass_rates.get(gid, 0.0)
        has_bug = self.critical_bugs.get(gid, False)
        threshold = GROUP_PASS_THRESHOLD_DEFAULT
        return rate >= threshold and not has_bug

    def is_complete(self) -> bool:
        """检查是否所有大组都已完成。"""
        threshold = GROUP_PASS_THRESHOLD_DEFAULT
        for gid in self.group_ids:
            rate = self.pass_rates.get(gid, 0.0)
            has_bug = self.critical_bugs.get(gid, False)
            if rate < threshold or has_bug:
                return False
        return True

    def overall_rate(self) -> float:
        """计算全局 overall 通过率。"""
        total_criteria = sum(len(g["criteria"]) for g in self.groups)
        passed_criteria = sum(
            len(g["criteria"]) * self.pass_rates.get(g["id"], 0.0)
            for g in self.groups
        )
        return passed_criteria / total_criteria if total_criteria > 0 else 0.0

    def check_group_passed(self, group_id: str) -> bool:
        """检查指定大组是否已通过（通过阈值且无 CRITICAL_BUG）。"""
        rate = self.pass_rates.get(group_id, 0.0)
        has_bug = self.critical_bugs.get(group_id, False)
        return rate >= GROUP_PASS_THRESHOLD_DEFAULT and not has_bug

    def any_group_stuck(self) -> tuple[bool, str | None]:
        """检查是否有大组连续卡死。"""
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
            "critical_bugs": dict(self.critical_bugs),
            "overall": self.overall_rate(),
        }

    @classmethod
    def from_dict(cls, data: dict, groups: list[dict]) -> "FeatureGroupState":
        """从字典恢复大组状态。"""
        state = cls(groups)
        state.current_idx = data.get("current_idx", 0)
        state.pass_rates = dict(data.get("pass_rates", {}))
        state.stuck_counts = dict(data.get("stuck_counts", {}))
        state.critical_bugs = dict(data.get("critical_bugs", {}))
        # Ensure current_idx is valid
        if state.current_idx >= len(state.group_ids):
            state.current_idx = len(state.group_ids) - 1
        if state.current_idx < 0:
            state.current_idx = 0
        log.info(
            f"[feature_groups] Restored from state: current={state.current_group_id}, "
            f"pass_rates={len(state.pass_rates)}, critical_bugs={sum(state.critical_bugs.values())}"
        )
        return state


def _check_exit_condition_dynamic(feature_groups: "FeatureGroupState") -> tuple[bool, str]:
    """动态退出条件：所有大组通过即可退出。"""
    if not feature_groups:
        return False, "No feature groups"

    if not feature_groups.is_complete():
        current = feature_groups.current_group
        current_id = current["id"] if current else "?"
        current_rate = feature_groups.pass_rates.get(current_id, 0.0)
        has_bug = feature_groups.critical_bugs.get(current_id, False)
        return False, (
            f"Group {current_id} not passed: {current_rate:.0%}, "
            f"critical_bug={has_bug}"
        )

    overall = feature_groups.overall_rate()
    if overall < OVERALL_PASS_THRESHOLD:
        return False, f"Overall {overall:.0%} below threshold {OVERALL_PASS_THRESHOLD:.0%}"

    # 检查 stuck groups
    stuck, stuck_gid = feature_groups.any_group_stuck()
    if stuck:
        return False, f"Group {stuck_gid} stuck for {feature_groups.stuck_counts.get(stuck_gid, 0)} rounds"

    return True, f"All groups passed, overall {overall:.0%}"


def get_group_instruction(group_id: str, group: dict) -> str:
    """生成大组的中文说明，注入 Builder prompt。"""
    lines = [
        f"## 当前 Sprint 目标：{group_id} — {group['name']}",
        f"",
        f"本轮你只需实现并完善 **{group_id}** 的功能。",
        f"验收标准：contract.md 中 {group_id} 的 {len(group['criteria'])} 项标准。",
        f"",
    ]

    # 添加子功能列表
    sub_features = group.get("sub_features", [])
    if sub_features:
        lines.append(f"**组内实现顺序**：")
        for i, sub in enumerate(sub_features, 1):
            lines.append(f"{i}. {sub['name']} ({len(sub['criteria'])} 项标准)")
        lines.append("")

    lines.extend([
        f"**重要规则**：",
        f"- 按组内顺序实现，不要跳过",
        f"- 不要实现其他大组（如 {group_id} 以外的）的内容",
        f"- 如果 {group_id} 的代码已部分存在，检查并修复它",
        f"- 完成后用 git commit 提交",
    ])
    return "\n".join(lines)
