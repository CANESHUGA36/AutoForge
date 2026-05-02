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
# Generate Image API key — used for image-01 generation.
# Falls back to the chat API key when unset.
GENERATE_IMAGE_API_KEY = os.environ.get("GENERATE_IMAGE_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "") or API_KEY
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("HARNESS_MODEL", "gpt-4o")

# Token 阈值 (200K context model — raise to keep more history before state injection)
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD", "180000"))
RESET_THRESHOLD = int(os.environ.get("RESET_THRESHOLD", "200000"))

# Harness 循环
MAX_ROUNDS = int(os.environ.get("MAX_HARNESS_ROUNDS", "0"))  # 0 = 动态计算
MIN_ROUNDS = 3
MAX_ROUNDS_HARD = 20
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "7.0"))

# 双轨评分 —— Sprint 过程门槛 + Overall 交付门槛
SPRINT_PASS_THRESHOLD = float(os.environ.get("SPRINT_PASS_THRESHOLD", "6.0"))
SIGNIFICANT_DROP = float(os.environ.get("SIGNIFICANT_DROP", "1.0"))

# 双轨通过率评分体系
SPRINT_PASS_RATE_THRESHOLD = float(os.environ.get("SPRINT_PASS_RATE_THRESHOLD", "0.70"))
CONTRACT_PASS_RATE_THRESHOLD = float(os.environ.get("CONTRACT_PASS_RATE_THRESHOLD", "0.75"))

# ============================================================================
# 分层验证体系权重与阈值（Validation Architecture v2）
# ============================================================================
# 四层验证：代码审查 + 契约测试 + React DevTools + 浏览器测试
# 总分 = Σ(维度得分 × 权重)，同时必须满足各维度最低要求

EVALUATION_WEIGHTS: dict[str, float] = {
    "code_review":   float(os.environ.get("WEIGHT_CODE_REVIEW",   "0.40")),
    "contract_tests": float(os.environ.get("WEIGHT_CONTRACT_TESTS", "0.35")),
    "react_devtools": float(os.environ.get("WEIGHT_REACT_DEVTOOLS", "0.15")),
    "browser_tests":  float(os.environ.get("WEIGHT_BROWSER_TESTS",  "0.10")),
}

# Next.js 项目特殊权重（SSR 降低浏览器测试权重）
NEXTJS_EVALUATION_WEIGHTS: dict[str, float] = {
    "code_review":    float(os.environ.get("WEIGHT_CODE_REVIEW",    "0.35")),
    "contract_tests":  float(os.environ.get("WEIGHT_CONTRACT_TESTS",  "0.35")),
    "react_devtools":  float(os.environ.get("WEIGHT_REACT_DEVTOOLS",  "0.15")),
    "ssr_check":       float(os.environ.get("WEIGHT_SSR_CHECK",       "0.10")),
    "browser_tests":   float(os.environ.get("WEIGHT_BROWSER_TESTS",   "0.05")),
}

# 纯 HTML 项目权重（浏览器测试更可靠）
HTML_EVALUATION_WEIGHTS: dict[str, float] = {
    "code_review":    float(os.environ.get("WEIGHT_CODE_REVIEW",    "0.30")),
    "contract_tests":  float(os.environ.get("WEIGHT_CONTRACT_TESTS",  "0.30")),
    "browser_tests":   float(os.environ.get("WEIGHT_BROWSER_TESTS",   "0.40")),
}

# Tier 阈值 — 仅 tier1/tier2 两个功能层级
# Functional 组按 50/50 分割：前一半 → tier1 (MVP)，后一半 → tier2 (Core)
# D/T 组（Design/Technical）不纳入退出判定，不阻塞项目成功
TIER_THRESHOLDS: dict[str, float] = {
    "tier1": float(os.environ.get("THRESHOLD_TIER1", "0.80")),  # MVP: 80%（降低避免过度严苛）
    "tier2": float(os.environ.get("THRESHOLD_TIER2", "0.70")),  # Core: 70%
}

# 维度最低要求已移除 —— 加权总分是唯一判定标准
# 旧逻辑：即使总分达标，任一维度低于阈值也判定失败
# 新逻辑：总分达标即通过，避免单一维度波动导致整体失败
DIMENSION_MINIMUMS: dict[str, dict[str, float]] = {}

# 契约测试配置
CONTRACT_TEST_ENABLED = os.environ.get("CONTRACT_TEST_ENABLED", "true").lower() == "true"
CONTRACT_TEST_SCORE_THRESHOLD = float(os.environ.get("CONTRACT_TEST_SCORE_THRESHOLD", "60.0"))

# React DevTools 配置
REACT_DEVTOOLS_ENABLED = os.environ.get("REACT_DEVTOOLS_ENABLED", "true").lower() == "true"
REACT_DEVTOOLS_SCORE_THRESHOLD = float(os.environ.get("REACT_DEVTOOLS_SCORE_THRESHOLD", "50.0"))

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
    # FIX: Increase Builder budget for first round (complex project setup)
    "builder": int(os.environ.get("MAX_ITERATIONS_BUILDER", "60")),
    # FIX: Increase Reviewer budget when Builder hits limits
    "reviewer": int(os.environ.get("MAX_ITERATIONS_REVIEWER", "50")),
    # FIX: Increase Judge budget for thorough evaluation
    "judge": int(os.environ.get("MAX_ITERATIONS_JUDGE", "15")),
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
SPRINT_CONTRACT_FILE = "sprint_contract.md"

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
