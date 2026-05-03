"""
提示词定义 —— 从 prompts/ 目录懒加载 .md 文件

保持向后兼容：所有常量仍可直接导入，但底层从 .md 文件读取。
加载时替换模板变量（如 {{WORKSPACE}} → 实际 workspace 路径）。

注意：prompts 在 Harness 初始化后会自动刷新，以获取正确的 WORKSPACE 路径。
"""
from pathlib import Path

import config

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """从 prompts/ 目录加载指定名称的 .md 文件，带缓存，并替换模板变量。"""
    if name not in _CACHE:
        path = _PROMPTS_DIR / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        content = path.read_text(encoding="utf-8")
        # 替换模板变量
        # file:// URL 需要正斜杠路径（跨平台兼容）
        workspace_path = Path(config.WORKSPACE).resolve().as_posix()
        content = content.replace("{{WORKSPACE}}", workspace_path)
        _CACHE[name] = content
    return _CACHE[name]


def refresh_prompts() -> None:
    """清空缓存，强制下次加载时重新读取并替换模板变量。

    在 Harness 初始化后调用，以确保 {{WORKSPACE}} 等变量使用最新值。
    """
    _CACHE.clear()


# 新 5-Agent 架构 —— 使用 property-like 访问，确保每次获取时都是最新的
# 由于现有代码大量直接使用 ARCHITECT_SYSTEM 等常量，我们保留这些名称
# 但底层通过 _PromptProxy 在访问时动态加载
class _PromptProxy:
    """Prompt 代理，访问时动态加载最新内容。"""

    def __init__(self, name: str):
        self._name = name

    def __str__(self) -> str:
        return load_prompt(self._name)

    def __repr__(self) -> str:
        return f"_PromptProxy({self._name!r})"

    def __contains__(self, item: str) -> bool:
        return item in str(self)

    def __iter__(self):
        return iter(str(self))

    def __len__(self) -> int:
        return len(str(self))

    def __getitem__(self, index):
        return str(self)[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(str(self))

    def startswith(self, prefix: str) -> bool:
        return str(self).startswith(prefix)

    def endswith(self, suffix: str) -> bool:
        return str(self).endswith(suffix)

    def split(self, sep: str | None = None, maxsplit: int = -1):
        return str(self).split(sep, maxsplit)

    def replace(self, old: str, new: str, count: int = -1) -> str:
        return str(self).replace(old, new, count)

    def format(self, *args, **kwargs) -> str:
        return str(self).format(*args, **kwargs)

    def strip(self, chars: str | None = None) -> str:
        return str(self).strip(chars)

    def lower(self) -> str:
        return str(self).lower()

    def upper(self) -> str:
        return str(self).upper()

    def find(self, sub: str, start: int = 0, end: int | None = None) -> int:
        s = str(self)
        if end is None:
            end = len(s)
        return s.find(sub, start, end)

    def count(self, sub: str, start: int = 0, end: int | None = None) -> int:
        s = str(self)
        if end is None:
            end = len(s)
        return s.count(sub, start, end)


ARCHITECT_SYSTEM = _PromptProxy("architect")
SPRINT_MASTER_SYSTEM = _PromptProxy("sprint_master")
BUILDER_SYSTEM = _PromptProxy("builder")
REVIEWER_SYSTEM = _PromptProxy("reviewer")

# 已删除的旧 prompt（不再使用）：
# JUDGE_SYSTEM, PLANNER_SYSTEM, CONTRACT_BUILDER_SYSTEM, SPRINT_PLANNER_SYSTEM,
# SPRINT_CONTRACT_BUILDER_SYSTEM, CODE_REVIEWER_SYSTEM,
# BROWSER_TESTER_SYSTEM, EVALUATOR_SYSTEM
