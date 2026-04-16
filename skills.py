"""
Skill 系统 - 渐进式披露
"""
from pathlib import Path


def build_catalog_prompt() -> str:
    """构建 skill 目录提示"""
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.exists():
        return ""

    skills = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if skill_dir.is_dir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                # 解析 frontmatter
                name = skill_dir.name
                description = ""
                if content.startswith("---"):
                    _, frontmatter, body = content.split("---", 2)
                    for line in frontmatter.splitlines():
                        if line.strip().startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                skills.append(f"- {name}: {description}")

    if not skills:
        return ""

    return "\nAvailable skills (use read_skill_file to load):\n" + "\n".join(skills)


def get_skill_path(name: str) -> Path | None:
    """获取 skill 文件路径"""
    skills_dir = Path(__file__).parent / "skills"
    skill_file = skills_dir / name / "SKILL.md"
    if skill_file.exists():
        return skill_file
    return None
