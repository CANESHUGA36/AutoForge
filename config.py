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
# MiniMax API key — used for image-01 generation, coding_plan search, and VLM vision.
# These are MiniMax-specific endpoints; falls back to the chat API key when unset.
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "") or API_KEY
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("HARNESS_MODEL", "gpt-4o")

# Token 阈值
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD", "80000"))
RESET_THRESHOLD = int(os.environ.get("RESET_THRESHOLD", "150000"))

# Harness 循环
MAX_ROUNDS = int(os.environ.get("MAX_HARNESS_ROUNDS", "0"))  # 0 = 动态计算
MIN_ROUNDS = 3
MAX_ROUNDS_HARD = 12
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "7.0"))

# 双轨评分 —— Sprint 过程门槛 + Overall 交付门槛
SPRINT_PASS_THRESHOLD = float(os.environ.get("SPRINT_PASS_THRESHOLD", "6.0"))
SIGNIFICANT_DROP = float(os.environ.get("SIGNIFICANT_DROP", "1.0"))

# 双轨通过率评分体系
SPRINT_PASS_RATE_THRESHOLD = float(os.environ.get("SPRINT_PASS_RATE_THRESHOLD", "0.70"))
CONTRACT_PASS_RATE_THRESHOLD = float(os.environ.get("CONTRACT_PASS_RATE_THRESHOLD", "0.75"))

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
MAX_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "50"))
UNIT_TEST_COVERAGE_THRESHOLD = float(os.environ.get("UNIT_TEST_THRESHOLD", "0.0"))
MAX_TOOL_ERRORS = 5

# Per-agent 迭代限制（覆盖 MAX_ITERATIONS）
AGENT_ITERATION_LIMITS = {
    "architect": int(os.environ.get("MAX_ITERATIONS_ARCHITECT", "30")),
    "sprint_master": int(os.environ.get("MAX_ITERATIONS_SPRINT_MASTER", "15")),
    "builder": int(os.environ.get("MAX_ITERATIONS_BUILDER", "50")),
    "reviewer": int(os.environ.get("MAX_ITERATIONS_REVIEWER", "30")),
    "judge": int(os.environ.get("MAX_ITERATIONS_JUDGE", "10")),
}

# 路径
WORKSPACE = os.path.abspath(os.environ.get("HARNESS_WORKSPACE", "./workspace"))

# Parent directory for auto-generated per-run workspaces when harness.py is run without
# --workspace. Default ./projects; in Docker set HARNESS_PROJECTS_DIR=/projects to match
# the volume mount (./projects:/projects).
PROJECTS_DIR = os.path.abspath(os.environ.get("HARNESS_PROJECTS_DIR", "./projects"))

SPEC_FILE = "spec.md"
FEEDBACK_FILE = "feedback.md"
CONTRACT_FILE = "contract.md"

PROGRESS_FILE = "progress.md"
SPRINT_FILE = "sprint.md"
STATE_FILE = "harness_state.json"

# Dev Server 配置
DEV_SERVER_PORTS = {
    "nextjs": 3000,
    "vite": 5173,
    "static": 3000,
}
DEV_SERVER_DEFAULT_WAIT = 10  # 启动后等待秒数
DEV_SERVER_HEALTH_CHECK_TIMEOUT = 10  # curl 健康检查超时
DEV_SERVER_MAX_WAIT = 30  # Harness 层验证最大等待时间

# 超时配置（秒）
TIMEOUT_TOOL_DEFAULT = 120
TIMEOUT_AGENT = 3600
TIMEOUT_PROJECT_INIT = 600
TIMEOUT_BUILD = 180
TIMEOUT_QUICK_CHECK = 60
TIMEOUT_BROWSER_TEST = 120
TIMEOUT_BROWSER_GOTO = 15
TIMEOUT_BROWSER_ACTION = 5

# Use UTF-8 for subprocess text mode; Windows default (e.g. cp936) breaks on UTF-8 bytes from npm/node/git.
SUBPROCESS_TEXT_KWARGS = {"encoding": "utf-8", "errors": "replace"}
