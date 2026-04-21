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


# 向后兼容：所有原有常量仍可直接导入
PLANNER_SYSTEM = load_prompt("planner")
BUILDER_SYSTEM = load_prompt("builder")
EVALUATOR_SYSTEM = load_prompt("evaluator")
SPRINT_PLANNER_SYSTEM = load_prompt("sprint_planner")
SPRINT_CONTRACT_BUILDER_SYSTEM = load_prompt("sprint_contract_builder")
CONTRACT_BUILDER_SYSTEM = load_prompt("contract_builder")
CODE_REVIEWER_SYSTEM = load_prompt("code_reviewer")
BROWSER_TESTER_SYSTEM = load_prompt("browser_tester")
COMPONENT_BUILDER_SYSTEM = load_prompt("component_builder")

# 已删除（未在 harness 中使用）：
# CONTRACT_REVIEWER_SYSTEM
# SPRINT_CONTRACT_REVIEWER_SYSTEM
