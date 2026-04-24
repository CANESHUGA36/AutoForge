"""
提示词定义 —— 从 prompts/ 目录懒加载 .md 文件

保持向后兼容：所有常量仍可直接导入，但底层从 .md 文件读取。
"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """从 prompts/ 目录加载指定名称的 .md 文件，带缓存。"""
    if name not in _CACHE:
        path = _PROMPTS_DIR / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        _CACHE[name] = path.read_text(encoding="utf-8")
    return _CACHE[name]


# 新 5-Agent 架构
ARCHITECT_SYSTEM = load_prompt("architect")
SPRINT_MASTER_SYSTEM = load_prompt("sprint_master")
BUILDER_SYSTEM = load_prompt("builder")
REVIEWER_SYSTEM = load_prompt("reviewer")
JUDGE_SYSTEM = load_prompt("judge")

# 保留的子代理 prompt
COMPONENT_BUILDER_SYSTEM = load_prompt("component_builder")
UNIT_TESTER_SYSTEM = load_prompt("unit_tester")

# 已删除的旧 prompt（不再使用）：
# PLANNER_SYSTEM, CONTRACT_BUILDER_SYSTEM, SPRINT_PLANNER_SYSTEM,
# SPRINT_CONTRACT_BUILDER_SYSTEM, CODE_REVIEWER_SYSTEM,
# BROWSER_TESTER_SYSTEM, EVALUATOR_SYSTEM
