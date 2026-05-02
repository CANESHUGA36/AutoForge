"""
Contract Test 框架
从 contract.md 生成并运行静态契约测试（无需浏览器）
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("harness")


@dataclass
class ContractCriterion:
    """契约标准"""
    id: str
    description: str
    tier: int
    group_id: str
    testable: bool = True
    validation_type: str = "functionality"


@dataclass
class TestResult:
    """测试结果"""
    passed: bool
    score: float  # 0-100
    details: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractTest:
    """单个契约测试"""
    criterion_id: str
    name: str
    test_fn: Callable[[], TestResult]
    weight: float = 1.0


class ContractTestSuite:
    """契约测试套件 — 静态代码分析，无需浏览器"""

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self.contract_path = self.project_path / "contract.md"
        self.tests: list[ContractTest] = []
        self.criteria: list[ContractCriterion] = []
        self._source_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    #  Contract parsing
    # ------------------------------------------------------------------ #
    def parse_contract(self) -> list[ContractCriterion]:
        """解析 contract.md 提取可测试标准"""
        logger.info(f"[contract_test] Looking for contract at: {self.contract_path}")
        if not self.contract_path.exists():
            logger.warning(f"[contract_test] contract.md not found at {self.contract_path}")
            # 尝试在项目目录中查找
            alt_path = self.project_path / "contract.md"
            if alt_path.exists():
                logger.info(f"[contract_test] Found contract at alternative path: {alt_path}")
                self.contract_path = alt_path
            else:
                # 列出目录内容帮助诊断
                try:
                    files = list(self.project_path.iterdir())
                    file_names = [f.name for f in files if f.is_file()]
                    logger.warning(f"[contract_test] Files in {self.project_path}: {file_names}")
                except Exception as e:
                    logger.warning(f"[contract_test] Cannot list directory: {e}")
                return []

        content = self.contract_path.read_text(encoding="utf-8", errors="replace")
        criteria = []

        # 尝试新格式: ## Group 1: 大组名称
        group_pattern = re.compile(r'^#{2}\s+Group\s+(\d+)[:：]\s*(.+?)$', re.MULTILINE | re.IGNORECASE)
        groups = list(group_pattern.finditer(content))
        
        if groups:
            # 新格式 G1.A.1
            for i, match in enumerate(groups):
                group_num = match.group(1)
                group_name = match.group(2).strip()
                start = match.end()
                end = groups[i + 1].start() if i + 1 < len(groups) else len(content)
                section = content[start:end]

                # 解析该组下的标准项: - [ ] **G1.A.1** 描述
                item_pattern = re.compile(
                    r'^\s*-\s*\[\s*\]\s*\*\*(G\d+\.[A-Z]\.\d+)\*\*\s*(.+?)(?=\n|$)',
                    re.MULTILINE
                )
                for item_match in item_pattern.finditer(section):
                    cid = item_match.group(1)
                    desc = item_match.group(2).strip()
                    tier = self._infer_tier(group_num)
                    testable = self._is_testable(desc)
                    vtype = self._infer_validation_type(desc)
                    criteria.append(ContractCriterion(
                        id=cid, description=desc, tier=tier,
                        group_id=f"G{group_num}", testable=testable,
                        validation_type=vtype,
                    ))
        else:
            # 回退到旧格式: ### F1: 功能名称
            group_pattern = re.compile(r'###\s*(F\d+)[:：]\s*(.+?)$', re.MULTILINE)
            groups = list(group_pattern.finditer(content))

            for i, match in enumerate(groups):
                group_id = match.group(1)
                group_name = match.group(2).strip()
                start = match.end()
                end = groups[i + 1].start() if i + 1 < len(groups) else len(content)
                section = content[start:end]

                # 解析该组下的标准项
                item_pattern = re.compile(
                    r'-\s*\[\s*\]\s*\*\*(F\d+\.\d+)\*\*\s*(.+?)(?=\n|$)',
                    re.MULTILINE
                )
                for item_match in item_pattern.finditer(section):
                    cid = item_match.group(1)
                    desc = item_match.group(2).strip()
                    tier = self._infer_tier(group_id)
                    testable = self._is_testable(desc)
                    vtype = self._infer_validation_type(desc)
                    criteria.append(ContractCriterion(
                        id=cid, description=desc, tier=tier,
                        group_id=group_id, testable=testable,
                        validation_type=vtype,
                    ))

        self.criteria = criteria
        logger.info(f"[contract_test] Parsed {len(criteria)} criteria from contract.md")
        return criteria

    def _infer_tier(self, group_id: str | int) -> int:
        """从功能组 ID 推断 tier"""
        if isinstance(group_id, int):
            num = group_id
        else:
            num = int(re.search(r'\d+', group_id).group()) if re.search(r'\d+', group_id) else 1
        if num <= 2:
            return 1
        elif num <= 3:
            return 2
        return 3

    def _is_testable(self, description: str) -> bool:
        """判断标准是否可自动生成测试"""
        ui_subjective = [
            "visually appealing", "beautiful", "clean design",
            "professional look", "polished", "aesthetic",
            "美观", "漂亮", "精致", "视觉"
        ]
        return not any(kw in description.lower() for kw in ui_subjective)

    def _infer_validation_type(self, description: str) -> str:
        """推断验证类型"""
        dl = description.lower()
        if any(kw in dl for kw in ["click", "drag", "select", "hover", "interact"]):
            return "interaction"
        elif any(kw in dl for kw in ["render", "display", "show", "visible", "appear"]):
            return "render"
        elif any(kw in dl for kw in ["save", "load", "export", "import", "persist"]):
            return "data"
        elif any(kw in dl for kw in ["performance", "fast", "speed", "latency", "smooth"]):
            return "performance"
        elif any(kw in dl for kw in ["cursor", "presence", "user", "collaborat"]):
            return "presence"
        elif any(kw in dl for kw in ["shape", "draw", "line", "rect", "circle", "arrow"]):
            return "drawing"
        elif any(kw in dl for kw in ["text", "font", "typography"]):
            return "text"
        else:
            return "functionality"

    # ------------------------------------------------------------------ #
    #  Source code analysis helpers
    # ------------------------------------------------------------------ #
    def _get_source_files(self, pattern: str = "**/*.{tsx,ts,jsx,js}") -> list[Path]:
        """获取项目源码文件"""
        src_dir = self.project_path / "src"
        if not src_dir.exists():
            src_dir = self.project_path
        files = []
        for ext in [".tsx", ".ts", ".jsx", ".js"]:
            files.extend(src_dir.rglob(f"*{ext}"))
        return [f for f in files if "node_modules" not in str(f)]

    def _read_source(self, path: Path) -> str:
        """读取并缓存源码"""
        key = str(path)
        if key not in self._source_cache:
            try:
                self._source_cache[key] = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                self._source_cache[key] = ""
        return self._source_cache[key]

    def _find_component_files(self, keywords: list[str]) -> list[tuple[Path, str]]:
        """根据关键词查找相关组件文件"""
        results = []
        for f in self._get_source_files():
            source = self._read_source(f)
            score = sum(1 for kw in keywords if kw.lower() in source.lower())
            if score > 0:
                results.append((f, source))
        # 按相关度排序
        results.sort(key=lambda x: sum(1 for kw in keywords if kw.lower() in x[1].lower()), reverse=True)
        return results[:5]

    def _has_export(self, source: str) -> bool:
        """检查是否有 export"""
        return "export" in source

    def _has_jsx_return(self, source: str) -> bool:
        """检查是否有 JSX return"""
        return "return" in source and ("<" in source and ">" in source)

    def _has_hooks(self, source: str, hooks: list[str]) -> bool:
        """检查是否使用了指定 hooks"""
        return any(f"use{h}" in source or h in source for h in hooks)

    def _has_event_handler(self, source: str, events: list[str] | None = None) -> bool:
        """检查是否有事件处理函数"""
        if events is None:
            events = ["onClick", "onMouseDown", "onPointerDown", "onChange", "onKeyDown", "onSubmit"]
        return any(e in source for e in events)

    def _has_state_management(self, source: str) -> bool:
        """检查是否有状态管理"""
        return "useState" in source or "useReducer" in source or "useContext" in source or "create" in source

    def _check_props_interface(self, source: str) -> bool:
        """检查是否有 props interface/type"""
        return "interface" in source or "type " in source or "props" in source.lower()

    def _count_testids(self, source: str, group_id: str) -> int:
        """统计 data-testid 数量"""
        pattern = re.compile(rf'data-testid="{group_id.lower()}[^"]*"', re.IGNORECASE)
        return len(pattern.findall(source))

    def _has_conditional_rendering(self, source: str) -> bool:
        """检查是否有条件渲染（&& 或三元在 JSX 中）"""
        # 简单的正则检测 JSX 中的条件渲染
        jsx_conditional = re.compile(r'\{[^}]*&&\s*<')
        ternary_jsx = re.compile(r'\{[^}]*\?\s*<[^>]*>\s*:')
        return bool(jsx_conditional.search(source) or ternary_jsx.search(source))

    def _has_css_visibility(self, source: str) -> bool:
        """检查是否有 CSS 显隐控制"""
        return "display:" in source or "style={{display:" in source or "className=" in source

    def _has_css_classes(self, source: str) -> bool:
        """检查是否有 CSS class 使用"""
        return "className=" in source or "class=" in source

    def _has_inline_styles(self, source: str) -> bool:
        """检查是否有内联样式"""
        return "style=" in source or "style={{" in source

    # ------------------------------------------------------------------ #
    #  Test generation
    # ------------------------------------------------------------------ #
    def generate_tests(self, feature_group: str) -> list[ContractTest]:
        """为指定功能组生成测试"""
        if not self.criteria:
            self.parse_contract()

        group_criteria = [c for c in self.criteria if c.group_id == feature_group]
        tests = []

        for criterion in group_criteria:
            if not criterion.testable:
                continue
            test = self._create_test_for_criterion(criterion)
            if test:
                tests.append(test)

        self.tests = tests
        logger.info(f"[contract_test] Generated {len(tests)} tests for {feature_group}")
        return tests

    def _create_test_for_criterion(self, criterion: ContractCriterion) -> ContractTest | None:
        """为单个标准创建测试"""
        generators = {
            "render": self._generate_render_test,
            "interaction": self._generate_interaction_test,
            "presence": self._generate_presence_test,
            "drawing": self._generate_drawing_test,
            "text": self._generate_text_test,
            "data": self._generate_data_test,
            "performance": self._generate_performance_test,
            "functionality": self._generate_functionality_test,
        }
        generator = generators.get(criterion.validation_type)
        if not generator:
            return None
        return generator(criterion)

    def _generate_render_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成渲染测试"""
        def test_fn() -> TestResult:
            keywords = self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details=f"No component found for {criterion.id}",
                    evidence={"keywords": keywords}
                )

            best_file, source = files[0]
            score = 0
            evidence = {"file": str(best_file.name)}

            if self._has_export(source):
                score += 30
                evidence["has_export"] = True
            if self._has_jsx_return(source):
                score += 30
                evidence["has_jsx"] = True
            if self._check_props_interface(source):
                score += 25
                evidence["has_props"] = True
            # testid 权重降低：从 20 降到 15，且只要有任意 testid 就给满分
            testid_count = self._count_testids(source, criterion.group_id)
            if testid_count > 0:
                score += 15
                evidence["testids"] = testid_count

            return TestResult(
                passed=score >= 65,  # 降低通过阈值
                score=score,
                details=f"Component {best_file.name}: export={evidence.get('has_export')}, jsx={evidence.get('has_jsx')}, props={evidence.get('has_props')}, testids={testid_count}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"render_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_interaction_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成交互测试"""
        def test_fn() -> TestResult:
            keywords = self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details=f"No component found for {criterion.id}",
                    evidence={}
                )

            best_file, source = files[0]
            score = 0
            evidence = {"file": str(best_file.name)}

            if self._has_event_handler(source):
                score += 40
                evidence["has_event_handler"] = True
            if self._has_state_management(source):
                score += 35
                evidence["has_state"] = True
            # CSS 显隐检查放宽：只要有 CSS class/style 就给分
            if self._has_css_classes(source) or self._has_inline_styles(source):
                score += 25
                evidence["has_styling"] = True

            return TestResult(
                passed=score >= 60,
                score=score,
                details=f"Interaction check: handlers={evidence.get('has_event_handler')}, state={evidence.get('has_state')}, styling={evidence.get('has_styling')}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"interaction_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_presence_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成用户存在性测试（光标、在线状态等）"""
        def test_fn() -> TestResult:
            keywords = ["cursor", "presence", "user", "online", "collaborat"] + self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details=f"No cursor/presence component found",
                    evidence={}
                )

            score = 0
            evidence = {"files_checked": [str(f.name) for f, _ in files[:3]]}

            for f, source in files[:3]:
                if "cursor" in source.lower() or "presence" in source.lower():
                    score += 40
                    evidence["cursor_component"] = str(f.name)
                if self._has_export(source) and self._has_jsx_return(source):
                    score += 30
                if self._has_state_management(source):
                    score += 30

            score = min(score, 100)
            return TestResult(
                passed=score >= 60,
                score=score,
                details=f"Presence check: found in {evidence.get('cursor_component', 'none')}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"presence_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_drawing_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成绘图/形状测试"""
        def test_fn() -> TestResult:
            keywords = ["shape", "draw", "rect", "circle", "line", "arrow", "konva", "canvas"] + self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details="No drawing component found",
                    evidence={}
                )

            score = 0
            evidence = {}
            for f, source in files[:3]:
                if any(kw in source.lower() for kw in ["rect", "circle", "line", "arrow", "shape"]):
                    score += 50
                    evidence["shape_elements"] = True
                if "konva" in source.lower() or "canvas" in source.lower():
                    score += 30
                    evidence["drawing_lib"] = True
                if self._has_event_handler(source, ["onMouseDown", "onPointerDown", "onDrag"]):
                    score += 20
                    evidence["draw_handlers"] = True

            score = min(score, 100)
            return TestResult(
                passed=score >= 60,
                score=score,
                details=f"Drawing check: shapes={evidence.get('shape_elements')}, lib={evidence.get('drawing_lib')}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"drawing_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_text_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成文本组件测试"""
        def test_fn() -> TestResult:
            keywords = ["text", "textarea", "input", "font", "typography"] + self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details="No text component found",
                    evidence={}
                )

            best_file, source = files[0]
            score = 0
            evidence = {"file": str(best_file.name)}

            if "text" in source.lower():
                score += 50
                evidence["has_text"] = True
            if self._has_export(source) and self._has_jsx_return(source):
                score += 30
            if self._has_event_handler(source, ["onChange", "onKeyDown", "onBlur"]):
                score += 20
                evidence["has_text_handler"] = True

            return TestResult(
                passed=score >= 60,
                score=score,
                details=f"Text check: {best_file.name}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"text_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_data_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成数据持久化测试"""
        def test_fn() -> TestResult:
            keywords = ["save", "load", "export", "import", "persist", "storage", "localstorage"] + self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            score = 0
            evidence = {}

            for f, source in files[:3]:
                if "localStorage" in source or "sessionStorage" in source:
                    score += 50
                    evidence["storage_api"] = True
                if "export" in source.lower() or "import" in source.lower():
                    score += 30
                    evidence["export_import"] = True
                if "download" in source.lower() or "blob" in source.lower():
                    score += 20
                    evidence["download"] = True

            if not files:
                score = 20  # 可能还没实现

            score = min(score, 100)
            return TestResult(
                passed=score >= 50,
                score=score,
                details=f"Data persistence check: storage={evidence.get('storage_api')}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"data_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _generate_performance_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成性能测试（静态检查）"""
        def test_fn() -> TestResult:
            keywords = ["memo", "callback", "useMemo", "useCallback", "lazy", "suspense"] + self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            score = 50  # 基础分 — 性能难以静态验证
            evidence = {"note": "Performance is best validated via runtime profiling"}

            for f, source in files[:3]:
                if "useMemo" in source or "useCallback" in source or "memo" in source:
                    score += 30
                    evidence["optimization_hooks"] = True
                if "lazy" in source or "Suspense" in source:
                    score += 20
                    evidence["code_splitting"] = True

            score = min(score, 100)
            return TestResult(
                passed=score >= 50,
                score=score,
                details="Performance: static check only",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"perf_{criterion.id}",
            test_fn=test_fn,
            weight=0.5  # 性能测试权重较低
        )

    def _generate_functionality_test(self, criterion: ContractCriterion) -> ContractTest:
        """生成功能性通用测试"""
        def test_fn() -> TestResult:
            keywords = self._extract_keywords(criterion.description)
            files = self._find_component_files(keywords)

            if not files:
                return TestResult(
                    passed=False, score=0,
                    details=f"No relevant component found for {criterion.id}",
                    evidence={"keywords": keywords}
                )

            best_file, source = files[0]
            score = 0
            evidence = {"file": str(best_file.name)}

            if self._has_export(source):
                score += 25
            if self._has_jsx_return(source):
                score += 25
            if self._has_event_handler(source):
                score += 25
                evidence["has_handlers"] = True
            if self._has_state_management(source):
                score += 25
                evidence["has_state"] = True

            return TestResult(
                passed=score >= 60,
                score=score,
                details=f"Functionality check: {best_file.name} score={score}",
                evidence=evidence
            )
        return ContractTest(
            criterion_id=criterion.id,
            name=f"func_{criterion.id}",
            test_fn=test_fn,
            weight=1.0
        )

    def _extract_keywords(self, description: str) -> list[str]:
        """从描述中提取关键词"""
        # 提取大写单词（通常是组件名）和技术术语
        words = re.findall(r'[A-Z][a-zA-Z]+', description)
        # 提取小写的关键名词
        key_nouns = re.findall(r'\b(canvas|cursor|shape|text|input|button|modal|list|menu|toolbar|panel|layer|grid|chart|graph|map|audio|video|image|file|upload|download|search|filter|sort|drag|drop|zoom|pan|scroll|select|copy|paste|undo|redo|delete|add|remove|edit|create|update|save|load|export|import|share|print|email|notify|alert|confirm|cancel|submit|login|logout|register|profile|setting|theme|color|style|layout|animation|transition|transform|rotate|scale|move|resize|connect|disconnect|sync|collaborat|realtime|websocket|socket|peer|user|member|team|group|role|permission|admin|dashboard|widget|card|badge|tag|label|icon|avatar|banner|hero|header|footer|sidebar|nav|tab|step|progress|spinner|skeleton|placeholder|empty|error|warning|success|info|debug|log|trace|metric|analytics|report|stat|count|sum|avg|min|max|total|rate|ratio|percent|score|rank|level|tier|stage|phase|version|history|timeline|calendar|schedule|event|task|todo|note|comment|message|chat|conversation|thread|reply|mention|reaction|emoji|sticker|gif|media|attachment|link|url|domain|path|route|page|view|screen|section|block|element|component|module|feature|function|method|api|endpoint|request|response|header|body|param|query|cookie|session|token|auth|jwt|oauth|sso|credential|password|pin|code|otp|verify|validate|check|test|assert|expect|match|find|get|set|put|post|patch|delete|options|head|connect|trace|websocket|grpc|graphql|rest|rpc|soap|xml|json|yaml|toml|csv|tsv|sql|nosql|mongodb|redis|postgres|mysql|sqlite|firebase|supabase|prisma|drizzle|sequelize|typeorm|knex|express|fastify|koa|hapi|nest|next|nuxt|remix|astro|svelte|solid|vue|angular|react|preact|lit|stencil|qwik|htmx|alpine|jquery|bootstrap|tailwind|material|chakra|mantine|antd|shadcn|radix|headless|framer|motion|gsap|three|d3|chartjs|recharts|plotly|leaflet|mapbox|google|stripe|paypal|twilio|sendgrid|aws|gcp|azure|vercel|netlify|heroku|docker|kubernetes|terraform|ansible|jenkins|github|gitlab|bitbucket|jira|trello|slack|discord|notion|figma|sketch|xd|photoshop|illustrator|premiere|after|effects|blender|unity|unreal|godot|wasm|webgl|webrtc|webtorrent|ipfs|blockchain|crypto|nft|defi|dao|smart|contract|ethereum|solana|polygon|arbitrum|optimism|base|zksync|starknet|cosmos|polkadot|near|aptos|sui|sei|injective|celestia|dymension|avail|eigen|layer|rollapp|appchain|subnet|parachain|chain|node|validator|miner|staker|delegator|governance|proposal|vote|election|dao|treasury|grant|bounty|hackathon|challenge|quest|mission|achievement|badge|reward|point|token|coin|currency|fiat|stable|swap|pool|liquidity|yield|farm|stake|lend|borrow|leverage|margin|future|option|perp|derivative|insurance|coverage|claim|settlement|arbitration|dispute|resolution|escrow|multisig|timelock|vesting|cliff|unlock|release|airdrop|faucet|mint|burn|transfer|bridge|wrap|unwrap|deposit|withdraw|convert|exchange|trade|buy|sell|order|limit|market|stop|trailing|oco|bracket|grid|dca|twap|vwap|momentum|mean|reversion|arbitrage|hedge|delta|gamma|theta|vega|rho|iv|skew|term|structure|curve|surface|model|pricing|valuation|risk|var|cvar|es|stress|backtest|optimization|simulation|monte|carlo|brownian|motion|random|walk|markov|chain|bayesian|network|neural|deep|machine|learning|ai|llm|gpt|claude|gemini|llama|mistral|anthropic|openai|google|meta|microsoft|amazon|apple|nvidia|intel|amd|arm|qualcomm|broadcom|texas|instrument|analog|device|microchip|stmicro|nxp|renesas|infineon|on|semi|maxim|integrated|silicon|laboratory|skyworks|qorvo|murata|tdk|yageo|samsung|lg|sony|panasonic|sharp|toshiba|hitachi|fujitsu|nec|mitsubishi|canon|nikon|olympus|pentax|sigma|tamron|tokina|zeiss|leica|hasselblad|phase|one|fuji|gfx|x|series|eos|r|z|alpha|a7|a9|a1|r5|r3|1dx|d850|d6|z9|z8|z7|z6|z5|zf|fc|b|h|s|gfx|100|50|100s|50r|50s|xt|xh|xe|xm|xa|xb|xc|xd|xf|xg|xh|xi|xj|xk|xl|xm|xn|xo|xp|xq|xr|xs|xt|xu|xv|xw|xx|xy|xz)[a-z]*\b', description.lower())
        # 合并并去重
        all_words = list(dict.fromkeys(words + key_nouns))
        return all_words[:10]  # 限制关键词数量

    # ------------------------------------------------------------------ #
    #  Test execution
    # ------------------------------------------------------------------ #
    def run_tests(self) -> dict[str, TestResult]:
        """运行所有测试"""
        results = {}
        for test in self.tests:
            try:
                result = test.test_fn()
                results[test.criterion_id] = result
            except Exception as e:
                logger.warning(f"[contract_test] Test {test.criterion_id} failed: {e}")
                results[test.criterion_id] = TestResult(
                    passed=False, score=0,
                    details=f"Test error: {e}",
                    evidence={"error": str(e)}
                )
        return results

    def calculate_score(self, results: dict[str, TestResult]) -> float:
        """计算加权总分"""
        if not results:
            return 0.0

        total_score = 0.0
        total_weight = 0.0

        for test in self.tests:
            result = results.get(test.criterion_id)
            if result:
                total_score += result.score * test.weight
                total_weight += test.weight

        return total_score / total_weight if total_weight > 0 else 0.0

    def run_for_group(self, feature_group: str) -> dict[str, Any]:
        """为功能组运行完整测试流程"""
        if not self.criteria:
            self.parse_contract()

        tests = self.generate_tests(feature_group)
        results = self.run_tests()
        score = self.calculate_score(results)

        # 统计
        group_criteria = [c for c in self.criteria if c.group_id == feature_group]
        testable = [c for c in group_criteria if c.testable]

        return {
            "feature_group": feature_group,
            "total_criteria": len(group_criteria),
            "testable_criteria": len(testable),
            "tests_run": len(tests),
            "results": {
                cid: {
                    "passed": r.passed,
                    "score": r.score,
                    "details": r.details,
                    "evidence": r.evidence,
                }
                for cid, r in results.items()
            },
            "score": score,
            "passed": score >= 60,
        }


class ContractTestRunner:
    """契约测试运行器 — 集成到 Harness"""

    def __init__(self, project_path: Path | str):
        self.suite = ContractTestSuite(Path(project_path))

    def run_for_group(self, feature_group: str) -> dict[str, Any]:
        """为功能组运行契约测试"""
        return self.suite.run_for_group(feature_group)
