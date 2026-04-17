"""
Harness 配置
"""
import os
from pathlib import Path


def _load_dotenv():
    """加载 .env 文件"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key:
            os.environ[key] = value


_load_dotenv()

# API 配置
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("HARNESS_MODEL", "gpt-4o")

# Token 阈值
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD", "80000"))
RESET_THRESHOLD = int(os.environ.get("RESET_THRESHOLD", "150000"))

# Harness 循环
MAX_ROUNDS = int(os.environ.get("MAX_HARNESS_ROUNDS", "5"))
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "7.0"))

# Per-dimension hard thresholds — if any dimension scores below its threshold,
# the sprint is forced to fail regardless of the overall score.
# Keys must match the canonical names returned by Harness._parse_dimension_scores().
DIMENSION_THRESHOLDS: dict = {
    "functionality":  float(os.environ.get("THRESHOLD_FUNCTIONALITY",  "5.0")),
    "design_quality": float(os.environ.get("THRESHOLD_DESIGN_QUALITY", "4.0")),
    "originality":    float(os.environ.get("THRESHOLD_ORIGINALITY",    "3.0")),
    "craft":          float(os.environ.get("THRESHOLD_CRAFT",          "3.0")),
}

# Agent 限制
MAX_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "80"))
MAX_TOOL_ERRORS = 5

# 路径
WORKSPACE = os.path.abspath(os.environ.get("HARNESS_WORKSPACE", "./workspace"))
SPEC_FILE = "spec.md"
FEEDBACK_FILE = "feedback.md"
CONTRACT_FILE = "contract.md"
PROGRESS_FILE = "progress.md"
SPRINT_FILE = "sprint.md"
