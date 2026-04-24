# AutoForge 重构方案 v1.0

> 本文档汇总所有需要改动、优化和新增的功能点，按模块分类，可直接交付 coding agent 执行。

---

## 目录

1. [Agent 架构简化：9 → 5](#一agent-架构简化9--5)
2. [Prompt 系统重构](#二prompt-系统重构)
3. [Harness 核心编排层调整](#三harness-核心编排层调整)
4. [Builder Prompt 瘦身](#四builder-prompt-瘦身)
5. [配置与数据流调整](#五配置与数据流调整)
6. [Playwright MCP 连接池优化](#六playwright-mcp-连接池优化)
7. [动态迭代预算](#七动态迭代预算)
8. [新增单元测试层 UnitTester](#八新增单元测试层-unittester)
9. [框架自身 pytest 测试](#九框架自身-pytest-测试)
10. [动态轮数限制](#十动态轮数限制)
11. [其他保留与删除清单](#十一其他保留与删除清单)
12. [工具集按 Agent 职能细分](#十二工具集按-agent-职能细分)
13. [上下文压缩策略升级](#十三上下文压缩策略升级)

---

## 一、Agent 架构简化：9 → 5

### 1.1 合并策略

| 新 Agent | 由哪些旧 Agent 合并 | 核心职责 | 输出文件 |
|---------|-------------------|---------|---------|
| **Architect** | Planner + ContractBuilder | 一次性产出产品规格 + 全局验收标准 | `spec.md` + `contract.md` |
| **SprintMaster** | SprintPlanner + SprintContractBuilder | 规划本轮聚焦任务 + 本轮验收标准 | `sprint.md`（自带验收标准） |
| **Builder** | Builder（瘦身）+ ComponentBuilder | 编写代码、构建验证、Git 提交 | 源代码文件 |
| **Reviewer** | CodeReviewer + BrowserTester | 统一代码审查 + 浏览器 E2E 测试 | 审查报告文本 |
| **Judge** | Evaluator（瘦身） | 基于 Reviewer 报告评分，不下场验证 | `feedback.md` |

### 1.2 合并逻辑说明

**为什么合并 Planner + ContractBuilder：**
- `spec.md` 和 `contract.md` 是同一颗硬币的两面——功能是正面，验收标准是反面
- 让一个 Agent 同时思考"做什么"和"怎么算做完"，上下文一致，避免功能写进去了但验收标准漏了
- 删除原 3 轮协商流程（Proposer + Reviewer 循环），改为一次性产出

**为什么合并 SprintPlanner + SprintContractBuilder：**
- `sprint.md` 的每个 Task 天然对应验收标准的每条 Criterion
- 旧流程：SprintPlanner 写任务 → SprintContractBuilder 加工标准 → Builder 读两份文件对齐
- 新流程：SprintMaster 写任务时直接附带验收标准，Builder 只读一份文件

**为什么合并 CodeReviewer + BrowserTester：**
- 原 Evaluator 需要把两份格式不同的报告缝合成一个评分，逻辑复杂且容易不一致
- 合并后 Reviewer 产出统一格式的审查报告，Judge 直接基于单一报告判分
- 消除 Evaluator 越界下场测试的模糊地带

**为什么 Evaluator 改为 Judge 并瘦身：**
- 原 Evaluator 同时当"运动员"（亲自 `browser_evaluate`、`analyze_image`）和"裁判"
- 新 Judge 只基于提供的材料做判断，不调用任何测试工具
- 模糊的反馈应写为"Reviewer 报告不充分"，而不是自己补测

---

## 二、Prompt 系统重构

### 2.1 删除的 Prompt 文件

以下 7 个文件删除，内容合并进新 prompt：

- `prompts/planner.md` → 并入 `prompts/architect.md`
- `prompts/contract_builder.md` → 并入 `prompts/architect.md`
- `prompts/sprint_planner.md` → 并入 `prompts/sprint_master.md`
- `prompts/sprint_contract_builder.md` → 并入 `prompts/sprint_master.md`
- `prompts/code_reviewer.md` → 并入 `prompts/reviewer.md`
- `prompts/browser_tester.md` → 并入 `prompts/reviewer.md`
- `prompts/evaluator.md` → 改为 `prompts/judge.md`

### 2.2 保留的 Prompt 文件

- `prompts/builder.md` —— 大幅瘦身（见第四节）
- `prompts/component_builder.md` —— 不变，仍通过 `delegate_task` 调用

### 2.3 新建的 Prompt 文件

#### `prompts/architect.md`

```markdown
你是 Architect。你的工作是理解用户需求，并一次性产出两份文档：产品规格（spec.md）和全局验收标准（contract.md）。

## 输入
用户的一句话需求（1-4 句话）。

## 输出步骤
1. 将用户需求扩展为全面的产品规格：
   - Overview：产品定位和用户价值
   - Features：功能列表（含用户故事）
   - Technical Stack：技术选型建议
   - Design Direction：视觉方向（配色、字体、布局哲学）
   - Asset Pipeline：如需图片资产，明确使用 generate_image 工具
2. 基于 spec.md，为每个功能条目编写对应的、可测试的验收标准：
   - Functional Criteria：每条标准必须有明确的 [PASS/FAIL] 判断条件
   - Design Criteria：视觉和交互要求
   - Technical Criteria：代码质量、类型安全、无障碍等要求
   - 如果规格涉及图片资产，验收标准中必须要求使用 generate_image，不允许 CSS 渐变或占位图
3. 使用 write_file 先保存 spec.md，再保存 contract.md。

## 规则
- 如果用户引用了外部网站或设计风格，先用 search_web 研究，再写入规格。
- 不要写任何实现代码。
- 不要读取 feedback.md 或 sprint.md——它们还不存在。
```

#### `prompts/sprint_master.md`

```markdown
你是 SprintMaster。你的工作是决定本轮 Builder 应该完成什么，以及怎样算完成。

## 输入材料
1. spec.md —— 完整产品规格
2. contract.md —— 全局验收标准
3. 当前 workspace 文件列表
4. 如果存在：上一轮 sprint.md 和 feedback.md

## 输出
一份 sprint.md，包含：
- 本轮目标（一句话）
- 任务列表（1-2 个任务，每个最多 3 个子任务）
- 验收标准（3-6 条，每条可独立验证）
- 预估迭代数（乐观/保守/超限建议）
- 本轮不做的事（明确排除，防止 Builder 发散）

## 任务规划规则

### 优先级
1. 如果 feedback.md 有 DIMENSION_FAIL 或 Critical Issue——修复它
2. 如果产品骨架不存在——先搭骨架（Type A）
3. 如果骨架有了但视觉层不完整——补视觉（Type B）
4. 如果视觉完整了——加功能（Type C）
5. 如果功能都齐了——修 bug / 打磨（Type D）

### 范围控制
- 最多 2 个任务。如果上一轮超时或未完成，减到 1 个。
- 每个任务最多 3 个子任务。
- 代码预算：单文件 HTML 不超过 600 行；多组件项目新增/修改不超过 400 行。
- 迭代预算：Builder 约 25 次迭代，复杂任务预留 5 次给构建修复。

### 预估迭代数
根据任务类型给出参考：
- Type A（骨架）：10-15 次
- Type B（视觉）：20-30 次
- Type C（功能）：15-25 次
- Type D（Bug 修复）：5-10 次

## 验收标准写法
每条标准必须满足：
- 可独立验证（浏览器截图可见、代码存在、HTTP 请求可测）
- 用 [PASS/FAIL] 格式
- 至少一条"负面测试"（如：点击无效按钮不应报错）
- 不要覆盖"本轮不做"的功能

## 你不做的事
- 不要指定具体技术方案（"用 useState 还是 useReducer"）
- 不要指定文件结构（"创建 src/components/Header.tsx"）
- 不要写实现代码

## 输出格式

```markdown
# Sprint {round_num}

## Sprint Type
(A / B / C / D)

## Goal
一句话描述本轮交付物。

## Tasks
- [ ] 任务 1：具体描述
  - [ ] 子任务 a
  - [ ] 子任务 b
- [ ] 任务 2：（可选）

## Acceptance Criteria
- [ ] C1: 具体可验证标准
- [ ] C2: 具体可验证标准
- [ ] N1: 负面测试

## Estimated Iterations
- 乐观：X 次
- 保守：Y 次
- 若超过 Z 次，建议拆分为两个 Sprint

## Out of Scope
- 功能 X

## Notes for Builder
- 优先级最高的验收标准：C1、C2
- 如果迭代即将耗尽，优先保证最高优先级的 2 条标准
```

使用 write_file 保存到 sprint.md。
```

#### `prompts/reviewer.md`

```markdown
你是 Reviewer。你的工作是同时完成代码审查和浏览器测试，产出统一的审查报告。

## Part 1: 代码审查

### 范围限制
- 最多检查 8 个文件，按优先级：
  1. `src/app/page.tsx`（或 `page.jsx`）
  2. `src/app/layout.tsx`
  3. 主要组件（最多 4 个）
  4. `contract.md` 或 `spec.md`
- 跳过：Hooks、Stores、Types、CSS（除非怀疑特定问题）

### 检查项
1. 架构：代码是否模块化、组织良好？
2. 缺失实现：存根函数、TODO、占位文本、空处理器
3. Type Safety 和错误处理
4. 合同中缺少对应代码的功能
5. 重复或冲突逻辑
6. 动画实现正确性

## Part 2: 浏览器测试

1. 使用 `start_dev_server()` 启动服务器（不要 `npm run dev &`）
2. 检查 package.json 确定项目类型：
   - Next.js: `start_dev_server(command="npm run dev", port=3000)`
   - Vite: `start_dev_server(command="npm run dev", port=5173)`
   - 单 HTML 文件: `start_dev_server(command="npx serve -s . -l 3000", port=3000)`
3. 对每个关键页面调用 `browser_test`：
   - 桌面端：默认视口（1280×720）
   - 移动端：viewport={"width": 375, "height": 812}
4. 用 `browser_evaluate` 做精确的 DOM 验证
5. 如果浏览器不可用，立即回退到 curl HTTP 验证

## 统一输出格式

```markdown
# Review Report — Round {round_num}

## Code Review
- Files examined: ...（列出文件）
- Critical issues: ...（阻塞性 bug）
- Warnings: ...（非阻塞问题）
- Feature coverage: X/Y features from contract appear implemented

## Browser Tests
- Server startup: PASS/FAIL
- Desktop test: PASS/FAIL — 具体证据
- Mobile test: PASS/FAIL — 具体证据
- DOM verification: ...（browser_evaluate 结果）

## Visual Quality
- Screenshots: ...（截图路径）
- Color accuracy: ...
- Layout issues: ...

## Overall Assessment
- Build status: PASS/FAIL
- Ready for scoring: YES/NO
- Key concerns: ...
```

## 规则
- 不读每个源文件，聚焦最重要文件
- 不运行代码审查和浏览器测试之外的工具
- 限制：10 次迭代以内
```

#### `prompts/judge.md`

```markdown
你是 Judge（首席 QA 法官）。你只基于提供的材料做判断，不亲自下场验证。

## 输入材料
1. Reviewer 的统一审查报告
2. sprint.md（本轮目标）
3. contract.md（全局验收标准）
4. 历史 feedback.md（验证问题修复情况）

## 你的工作
1. 逐维度评分（0-10）：
   - Functionality（硬门槛 5.0）
   - Design Quality（硬门槛 4.0）
   - Originality（硬门槛 3.0）
   - Craft（硬门槛 3.0）
2. 计算加权总分：
   `OVERALL = Functionality×0.40 + Design×0.30 + Originality×0.15 + Craft×0.15`
3. 输出 feedback.md

## 你不能做的事
- 不调用 browser_evaluate
- 不调用 analyze_image
- 不调用 list_files 检查资产
- 不启动 dev server
- 如果 Reviewer 报告不充分，在 feedback.md 中标注"Reviewer report insufficient on point X"

## 输出格式

```markdown
# QA Feedback

## Evaluation

### Design Quality: X/10
<evidence>
[DIMENSION_FAIL: design_quality — 仅当分数 < 4]

### Originality: X/10
<evidence>
[DIMENSION_FAIL: originality — 仅当分数 < 3]

### Craft: X/10
<evidence>
[DIMENSION_FAIL: craft — 仅当分数 < 3]

### Functionality: X/10
<将每个标准列为 [PASS] 或 [FAIL]>
[DIMENSION_FAIL: functionality — 仅当分数 < 5]

## Strengths
- ...

## Issues Found
- ...

## Actionable Recommendations
1. ...

## Scoring Summary
```
SPRINT_SCORE: X/10
OVERALL_SCORE: X/10
```
```

关键：SPRINT_SCORE 和 OVERALL_SCORE 必须各单独一行。
使用 write_file 保存到 feedback.md。
```

### 2.4 `prompts.py` 常量映射更新

**删除：**
```python
PLANNER_SYSTEM
CONTRACT_BUILDER_SYSTEM
SPRINT_PLANNER_SYSTEM
SPRINT_CONTRACT_BUILDER_SYSTEM
CODE_REVIEWER_SYSTEM
BROWSER_TESTER_SYSTEM
EVALUATOR_SYSTEM
```

**新增：**
```python
ARCHITECT_SYSTEM = load_prompt("architect")
SPRINT_MASTER_SYSTEM = load_prompt("sprint_master")
REVIEWER_SYSTEM = load_prompt("reviewer")
JUDGE_SYSTEM = load_prompt("judge")
```

**保留：**
```python
BUILDER_SYSTEM = load_prompt("builder")
COMPONENT_BUILDER_SYSTEM = load_prompt("component_builder")
```

---

## 三、Harness 核心编排层调整

### 3.1 `harness/core.py` —— `Harness.__init__`

**当前：**
```python
self.planner = Agent("Planner", PLANNER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
self.sprint_planner = Agent("SprintPlanner", SPRINT_PLANNER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS, use_state=True, logger=self.log)
self.evaluator = Agent("Evaluator", EVALUATOR_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, use_state=True, logger=self.log)
```

**改为：**
```python
self.architect = Agent("Architect", ARCHITECT_SYSTEM, TOOL_SCHEMAS, logger=self.log)
self.sprint_master = Agent("SprintMaster", SPRINT_MASTER_SYSTEM, TOOL_SCHEMAS, logger=self.log)
self.builder = Agent("Builder", BUILDER_SYSTEM, TOOL_SCHEMAS, use_state=True, logger=self.log)
self.reviewer = Agent("Reviewer", REVIEWER_SYSTEM, TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS, logger=self.log)
self.judge = Agent("Judge", JUDGE_SYSTEM, TOOL_SCHEMAS, logger=self.log)
```

### 3.2 `Harness.run()` —— Phase 重构

**当前流程：**
```
Phase 1: Plan (planner) → spec.md
Phase 2: Contract (negotiate_contract) → contract.md
Phase 3+: Build-Evaluate loop
```

**新流程：**
```
Phase 1: Design (architect) → spec.md + contract.md（一次性）
Phase 2+: Build-Evaluate loop
```

**具体改动：**
- 删除 `_negotiate_contract()` 调用
- 删除 `plan_sprint()` 在 `run()` 中的调用（移至 `_build_round` 内）
- `architect.run()` 的 task prompt 指导它先写 `spec.md`，再基于 spec 写 `contract.md`
- Harness 层检查两个文件都存在才继续

### 3.3 `_build_round()` —— 评估流程重写

**当前流程：**
```python
1. plan_sprint()
2. negotiate_contract()
3. builder.run()
4. git commit
5. [Build Gate]
6. _run_eval_parallel(code_reviewer, browser_tester)
7. evaluator.run()
8. parse_scores
```

**新流程：**
```python
1. sprint_master.run()      # 产出 sprint.md
2. builder.run()             # 读 sprint.md，写代码
3. git commit
4. [Build Gate]
5. reviewer.run()           # 统一审查报告
6. judge.run()              # 评分，写 feedback.md
7. parse_scores
```

**删除：**
- `_run_eval_parallel()` 方法 → Reviewer 单一 Agent 替代
- EvalCache 复杂摘要逻辑 → Judge 直接读 Reviewer 原始报告

### 3.4 `harness/sprint.py` —— 函数替换

**删除：**
```python
def negotiate_contract(workspace, round_num, log): ...
```

**重写：**
```python
def plan_sprint_master(workspace: Path, round_num: int, sprint_master: Agent, log) -> None:
    """由 SprintMaster Agent 直接产出 sprint.md（自带验收标准）"""
    task = f"""Plan sprint {round_num}.
Read spec.md and contract.md, list existing files, then write sprint.md.
"""
    sprint_master.run(task)
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        log.warning("SprintMaster did not create sprint.md")
```

---

## 四、Builder Prompt 瘦身

### 4.1 删除的内容

| 原内容 | 去向 |
|--------|------|
| 项目初始化规则（`create-next-app`、`npm create vite`） | Harness 层模板预置 |
| Dev Server 配置规则（`"dev": "npx serve..."`） | Harness 层预配置或模板自带 |
| TypeScript 配置规则（`noUnusedLocals`） | 模板自带 |
| 图片资产生成完整流程 | 保留但精简为"按需 generate_image" |

### 4.2 保留的核心指令

```markdown
你是 Builder。你的工作是编写代码。

## 工作流程
1. 读取 sprint.md（你的唯一任务列表和验收标准）
2. 读取 feedback.md（处理相关问题）
3. 加载相关技能（按需）
4. 编写代码（完整、可工作、无 stub）
5. 运行 npm run build 验证
6. 提交代码：git add -A && git commit -m "round N: <summary>"
7. 声明策略 REFINE/PIVOT

## 项目根目录规则
工作空间目录就是项目根目录。永远不要为项目创建子文件夹。

## 构建验证（关键）
写入或编辑源文件后，系统会自动运行 npm run build。
- 看到 [BUILD WARNING] 报错，修复后再继续。
- build 失败时，先 read_skill_file("build-troubleshooting")。
- 可显式调用 validate_build() 检查状态。

## 迭代预算
- 每轮预算由 SprintMaster 在 sprint.md 中指定（通常 20-30 次）。
- 如果已使用 >80% 预算，停止添加新功能，保验收标准中优先级最高的 2 条。
- 不要把迭代浪费在代码风格、删除未使用导入或轻微视觉调整上。

## 策略声明（强制——最后一条消息）

```
---
STRATEGY: REFINE
REASON: ...
```

或

```
---
STRATEGY: PIVOT
REASON: ...
NEW DIRECTION: ...
```

可用工具：read_file, write_file, edit_file, list_files, run_bash, read_skill_file, generate_image, delegate_task, validate_build, project_init。
```

---

## 五、配置与数据流调整

### 5.1 `config.py` —— 删除 sprint_contract 文件常量

**删除：**
```python
SPRINT_CONTRACT_FILE = "sprint_contract.md"
```

**保留：**
```python
SPRINT_FILE = "sprint.md"  # 格式升级，内含 Acceptance Criteria
```

### 5.2 文件数量：5 份 → 4 份

| 当前（5 份） | 新架构（4 份） |
|-------------|--------------|
| `spec.md` | `spec.md` |
| `contract.md` | `contract.md` |
| `sprint.md` | `sprint.md`（**自带验收标准**） |
| `sprint_contract.md` | ❌ **删除，内容并入 sprint.md** |
| `feedback.md` | `feedback.md` |

### 5.3 Builder 读取逻辑变化

**当前：**
```markdown
1. Read sprint.md
2. Read sprint_contract.md (or contract.md)
```

**新：**
```markdown
1. Read sprint.md（内含验收标准）
2. Read contract.md（全局标准，fallback）
```

### 5.4 `eval_cache.py` —— 简化

**当前职责：**
- 保存 CodeReviewer + BrowserTester 报告 → `.eval_cache/round_N_{code_review,browser}.md`
- 提取结构化摘要（critical_count, warning_count, coverage...）
- 为 Evaluator 构建 `<1500 字符摘要 + 历史趋势`

**新职责（简化）：**
- 保存 Reviewer 统一报告 → `.eval_cache/round_N_review.md`
- 删除"摘要提取"和"历史趋势拼接"逻辑
- Judge 直接读 Reviewer 原始报告，不依赖 EvalCache 做预处理

---

## 六、Playwright MCP 连接池优化

### 6.1 问题

当前每调用 `browser_test_mcp()` 新建一个 `PlaywrightMCPBridge` 实例：
- 启动 Chromium：~1.5-3 秒
- 单次调用总开销：2-4 秒
- 同轮 5 次调用 = 10-20 秒纯开销

### 6.2 改动方案：Bridge Pool

在 `tools/playwright_mcp.py` 中新增：

```python
import asyncio
from contextlib import asynccontextmanager

class PlaywrightMCPPool:
    """跨调用复用 Playwright + Browser 实例，按 context_id 隔离"""
    
    def __init__(self):
        self._bridges: dict[str, PlaywrightMCPBridge] = {}
        self._lock = asyncio.Lock()
    
    async def get(self, context_id: str = "default") -> PlaywrightMCPBridge:
        async with self._lock:
            if context_id not in self._bridges:
                bridge = PlaywrightMCPBridge()
                await bridge.start()
                self._bridges[context_id] = bridge
            return self._bridges[context_id]
    
    async def release(self, context_id: str):
        async with self._lock:
            if context_id in self._bridges:
                await self._bridges[context_id].close()
                del self._bridges[context_id]
    
    async def close_all(self):
        for bridge in self._bridges.values():
            await bridge.close()
        self._bridges.clear()

# 全局单例
_pool = PlaywrightMCPPool()

async def browser_test_mcp(url, actions, context_id="default"):
    bridge = await _pool.get(context_id)
    return await bridge.test(url, actions)

async def browser_evaluate_mcp(script, context_id="default"):
    bridge = await _pool.get(context_id)
    return await bridge.evaluate(script)
```

### 6.3 Harness 层清理

在 `harness/core.py _build_round()` 末尾（或 `finally` 块）添加：

```python
from tools.playwright_mcp import _pool

# 每轮结束后释放 Reviewer 的浏览器实例
await _pool.release("reviewer")
```

### 6.4 预期收益

- 同轮多次 `browser_test`：从 5×3 秒 = 15 秒 → 1×3 秒 + 4×200ms = **3.8 秒**
- BrowserTester 的 5 分钟超时里，实际测试时间占比从 ~70% 提升到 ~95%

---

## 七、动态迭代预算

### 7.1 问题

`MAX_ITERATIONS = 80` 和 Builder prompt 里的 `~25 iterations` 是硬编码：
- 小项目太宽裕，Builder 浪费时间在 polish 上
- 大项目不够，一轮只够初始化 + 写 1 个组件

### 7.2 改动方案

#### 1. SprintMaster 在 sprint.md 中预估迭代数

`sprint.md` 新增 `Estimated Iterations` 章节（见 SprintMaster prompt）。

#### 2. Harness 层动态注入预算

在 `harness/core.py _build_round()` 中：

```python
def _inject_iteration_budget(self, build_task: str) -> str:
    sprint_path = self.workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        return build_task
    
    # 从 sprint.md 解析预估迭代数（简单正则提取）
    text = sprint_path.read_text()
    import re
    match = re.search(r'- 保守：(\d+) 次', text)
    conservative = int(match.group(1)) if match else 25
    
    threshold = int(conservative * 0.8)
    budget_msg = f"""
## Iteration Budget
本轮保守预算：{conservative} 次迭代。
如果已使用 >{threshold} 次，停止添加新功能，优先保证验收标准中优先级最高的 2 条。
"""
    return build_task + budget_msg
```

#### 3. 历史基准数据库（可选增强）

新建文件 `memory/iteration_baseline.json`：

```json
{
    "task_types": {
        "A-skeleton": {"mean": 12, "std": 3, "samples": 15},
        "B-visual": {"mean": 24, "std": 5, "samples": 8},
        "C-feature": {"mean": 18, "std": 4, "samples": 22},
        "D-bugfix": {"mean": 8, "std": 2, "samples": 10}
    },
    "project_types": {
        "single-html": 0.6,
        "vite-react": 1.0,
        "nextjs-app": 1.3
    }
}
```

SprintMaster 参考历史均值 + 项目类型系数，给出更准确的预估。

#### 4. Builder 自我报告 + Harness 校准

每轮结束后，Builder 在最终消息中报告：
```markdown
ITERATIONS_USED: 17/25
REMAINING_TASKS: 1 (pagination not started)
```

Harness 层记录实际值，反馈给 SprintMaster 修正下一轮预估。

---

## 八、新增单元测试层 UnitTester

### 8.1 问题

当前测试只有两层：
- 静态审查（CodeReviewer 读代码）
- E2E 测试（BrowserTester 开浏览器）

缺少单元测试，无法覆盖：
- 纯函数（utils, formatters）
- React Hooks 状态流转
- 数据筛选/转换逻辑

### 8.2 新增 Agent：`prompts/unit_tester.md`

```markdown
你是 UnitTester。你的工作是编写并运行自动化单元测试。

## 工作流程
1. 读取 sprint.md 了解本轮需要实现的功能
2. 读取现有代码，识别需要测试的单元：
   - 纯函数（utils, helpers, formatters）
   - React Hooks（状态流转、副作用）
   - 数据转换逻辑（filter, sort, validate）
3. 编写测试文件：
   - 使用项目已有的测试框架（Vitest/Jest）
   - 如果没有，用 run_bash 安装 vitest：`npm install -D vitest`
   - 文件命名：`__tests__/{module}.test.ts` 或 `*.test.ts`
4. 运行测试：`npm test` 或 `npx vitest run`
5. 报告结果：通过数 / 失败数 / 覆盖率

## 规则
- 不要测试 UI 渲染（留给 BrowserTester）
- 不要测试框架自带功能（如 React 的 useState）
- 优先测试 sprint.md 验收标准中的核心逻辑
- 测试必须能独立运行，不依赖 dev server
```

### 8.3 项目模板预置测试框架

Harness 层初始化项目时，根据模板预置：

| 模板 | 预置 |
|------|------|
| `vite-react-ts` | `vitest` + `@testing-library/react` + `jsdom` |
| `nextjs-app` | `jest` + `@testing-library/react` |
| `single-html` | 跳过单元测试层 |

### 8.4 测试金字塔集成到 Harness 流程

当前流程：
```
Builder → Git Commit → Build Gate → CodeReviewer ∥ BrowserTester → Evaluator
```

新流程：
```
Builder → Git Commit → Build Gate
    → UnitTester（运行单元测试）
    → Reviewer（代码审查 + 浏览器 E2E）
    → Judge（综合评分，单元测试通过率作为参考）
```

### 8.5 覆盖率门槛（可选）

```python
# config.py 新增
UNIT_TEST_COVERAGE_THRESHOLD = float(os.environ.get("UNIT_TEST_THRESHOLD", "0.0"))
# 0.0 = 不强制；可选开启如 0.5
```

---

## 九、框架自身 pytest 测试

### 9.1 测试目录结构

```
tests/
├── conftest.py              # 共享 fixtures
├── test_eval.py             # parse_scores, parse_dimension_scores, thresholds
├── test_context.py          # count_tokens, compact_messages, safe_split_index
├── test_strategy.py         # parse_strategy
├── test_state.py            # StateManager 原子写入/恢复/清理
├── test_git.py              # commit_round, get_commit_for_round, rollback_to
├── test_tools_impl.py       # _resolve 路径沙箱、_smart_truncate
├── test_workspace_state.py  # update_from_tool_result 增量推断
└── test_harness_core.py     # Build Gate、回滚条件、动态轮数
```

### 9.2 优先级最高的测试模块

#### `test_eval.py`

```python
import pytest
from harness.eval import parse_scores, parse_dimension_scores, check_dimension_thresholds

def test_parse_scores_standard_format():
    text = "SPRINT_SCORE: 7.5/10\nOVERALL_SCORE: 6.0/10"
    sprint, overall = parse_scores(text)
    assert sprint == 7.5
    assert overall == 6.0

def test_parse_scores_missing():
    text = "OVERALL_SCORE: 8.0/10"
    sprint, overall = parse_scores(text)
    assert sprint == 0.0
    assert overall == 8.0

def test_check_dimension_thresholds_functionality_fail():
    scores = {"functionality": 4.0, "design_quality": 5.0}
    failed = check_dimension_thresholds(scores)
    assert "functionality" in failed

def test_check_dimension_thresholds_all_pass():
    scores = {"functionality": 6.0, "design_quality": 5.0}
    failed = check_dimension_thresholds(scores)
    assert failed == []
```

#### `test_context.py`

```python
from context import count_tokens, _safe_split_index

def test_safe_split_index_does_not_cut_tool_chain():
    messages = [
        {"role": "user", "content": "call tool"},
        {"role": "assistant", "content": "...", "tool_calls": [{}]},
        {"role": "tool", "content": "result"},
    ]
    idx = _safe_split_index(messages, keep_ratio=0.5)
    # 不能切在 assistant(tool_calls) 和 tool 之间
    assert not (messages[idx].get("role") == "assistant" and messages[idx].get("tool_calls"))
```

#### `test_tools_impl.py`

```python
import pytest
from tools_impl import _resolve
import config

def test_resolve_allows_workspace_subpath(tmp_path):
    config.WORKSPACE = str(tmp_path)
    p = _resolve("src/app/page.tsx")
    assert p == tmp_path / "src/app/page.tsx"

def test_resolve_blocks_escape_attempt():
    config.WORKSPACE = "/home/user/workspace"
    with pytest.raises(ValueError):
        _resolve("../../etc/passwd")
```

#### `test_state.py`

```python
from harness.state import StateManager
import json

def test_save_is_atomic(tmp_path):
    mgr = StateManager(tmp_path)
    mgr.save({"completed_rounds": 3})
    data = json.loads((tmp_path / "harness_state.json").read_text())
    assert data["completed_rounds"] == 3

def test_clear_removes_file(tmp_path):
    mgr = StateManager(tmp_path)
    mgr.save({"test": True})
    mgr.clear()
    assert not (tmp_path / "harness_state.json").exists()
```

### 9.3 `tests/conftest.py`

```python
import pytest
import tempfile
from pathlib import Path
import subprocess

@pytest.fixture
def mock_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    return ws
```

### 9.4 运行

```bash
pip install pytest pytest-cov
pytest tests/ --cov=harness --cov=tools_impl --cov=context --cov=workspace_state
```

---

## 十、动态轮数限制

### 10.1 问题

`MAX_ROUNDS = 5` 硬编码：
- 单页展示：5 轮多余
- 全栈电商：5 轮不够
- 连续 PIVOT：5 轮不够调整架构

### 10.2 改动方案

#### `config.py` 调整

```python
MAX_ROUNDS = int(os.environ.get("MAX_HARNESS_ROUNDS", "0"))  # 0 = 动态计算
MIN_ROUNDS = 3
MAX_ROUNDS_HARD = 10
```

#### `harness/core.py` 新增方法

```python
def _calculate_max_rounds(self) -> int:
    """基于项目复杂度、历史表现、Builder 策略动态调整轮数上限"""
    
    base = self._estimate_from_spec()
    runtime_adjust = self._runtime_adjustment()
    strategy_adjust = self._strategy_adjustment()
    
    max_rounds = min(base + runtime_adjust + strategy_adjust, config.MAX_ROUNDS_HARD)
    max_rounds = max(max_rounds, config.MIN_ROUNDS)
    
    self.log.info(f"[dynamic_rounds] base={base}, runtime={runtime_adjust}, "
                  f"strategy={strategy_adjust} → max_rounds={max_rounds}")
    return max_rounds

def _estimate_from_spec(self) -> int:
    """从 spec.md 解析功能点，估算基础轮数"""
    spec_path = self.workspace / config.SPEC_FILE
    if not spec_path.exists():
        return 5
    
    spec_text = spec_path.read_text()
    feature_lines = [l for l in spec_text.splitlines() 
                     if l.strip().startswith("-") and "feature" in l.lower()]
    feature_count = len(feature_lines)
    asset_count = spec_text.count("generate_image")
    
    rounds = 2  # 骨架 + 验收
    rounds += feature_count // 3
    rounds += asset_count // 2
    return min(rounds, 8)

def _runtime_adjustment(self) -> int:
    """基于已跑轮次表现，追加或缩减"""
    if not self.sprint_score_history:
        return 0
    
    # 信号 A：连续高分但 Overall 没过 → 功能多，需要更多轮
    recent = self.sprint_score_history[-2:]
    if len(recent) == 2 and all(s >= 8.0 for s in recent):
        if not self.overall_score_history or self.overall_score_history[-1] < config.PASS_THRESHOLD:
            return 2
    
    # 信号 B：连续停滞（Overall 几乎不变）→ 缩减，逼迫 PIVOT
    if len(self.overall_score_history) >= 3:
        last_three = self.overall_score_history[-3:]
        if max(last_three) - min(last_three) < 0.5:
            return -1
    
    # 信号 C：Overall 持续上升且已接近门槛 → 追加 1 轮冲刺
    if self.overall_score_history and self.overall_score_history[-1] >= 6.5:
        return 1
    
    return 0

def _strategy_adjustment(self) -> int:
    """Builder 策略信号"""
    if not self.strategy_history:
        return 0
    
    recent_strategies = [s["strategy"] for s in self.strategy_history[-2:]]
    
    # 连续 PIVOT：架构在重构，给额外轮次
    if recent_strategies == ["PIVOT", "PIVOT"]:
        return 2
    
    # 连续 REFINE 且分数上升：势头好，给 1 轮奖励
    if recent_strategies == ["REFINE", "REFINE"]:
        if len(self.overall_score_history) >= 2 and \
           self.overall_score_history[-1] > self.overall_score_history[-2]:
            return 1
    
    return 0
```

#### `Harness.run()` 中使用动态轮数

```python
max_rounds = self._calculate_max_rounds()
for round_num in range(start_round, max_rounds + 1):
    # ... 原逻辑
```

### 10.3 动态轮数的测试

```python
# tests/test_harness_core.py
def test_estimate_from_spec_features(mock_workspace, harness_instance):
    spec = mock_workspace / "spec.md"
    spec.write_text("\n".join([f"- Feature {i}: ..." for i in range(9)]))
    max_r = harness_instance._estimate_from_spec()
    assert max_r >= 5  # 9 个功能至少 2 + 9//3 = 5 轮

def test_runtime_adjustment_stagnation(mock_workspace, harness_instance):
    harness_instance.overall_score_history = [5.0, 5.1, 5.2]
    assert harness_instance._runtime_adjustment() == -1

def test_strategy_adjustment_double_pivot(mock_workspace, harness_instance):
    harness_instance.strategy_history = [
        {"strategy": "PIVOT", "reason": "bad"},
        {"strategy": "PIVOT", "reason": "still bad"},
    ]
    assert harness_instance._strategy_adjustment() == 2
```

---

## 十一、其他保留与删除清单

### 11.1 应保留的核心机制（AutoForge 比 Anthropic 原文更聪明的地方）

| 机制 | 保留原因 |
|------|---------|
| Sprint 聚焦（1-2 任务/轮） | 防止 Builder 在长周期项目中发散 |
| Git 每轮快照 + 双条件回滚 | Sprint 失败回滚 HEAD，Overall 下降回滚历史最佳 |
| Build Gate（build error 跳过评估） | 省 token，避免在坏代码上浪费评估资源 |
| 维度硬门槛（单维度低于阈值强制 cap 总分） | 防止"高分但核心功能缺失"的伪通过 |
| 策略声明 REFINE/PIVOT | 给 Harness 明确的回滚/继续信号 |
| 状态持久化（harness_state.json 原子写入） | 支持崩溃恢复 |
| 上下文压缩三级策略 | 防止长运行中的 token 爆炸 |
| 路径沙箱（`_resolve`） | 安全边界 |

### 11.2 应删除的项

| 删除对象 | 位置 | 原因 |
|---------|------|------|
| `prompts/planner.md` | `prompts/` | 并入 architect |
| `prompts/contract_builder.md` | `prompts/` | 并入 architect |
| `prompts/sprint_planner.md` | `prompts/` | 并入 sprint_master |
| `prompts/sprint_contract_builder.md` | `prompts/` | 并入 sprint_master |
| `prompts/code_reviewer.md` | `prompts/` | 并入 reviewer |
| `prompts/browser_tester.md` | `prompts/` | 并入 reviewer |
| `prompts/evaluator.md` | `prompts/` | 改为 judge |
| `PLANNER_SYSTEM` | `prompts.py` | 常量删除 |
| `CONTRACT_BUILDER_SYSTEM` | `prompts.py` | 常量删除 |
| `SPRINT_PLANNER_SYSTEM` | `prompts.py` | 常量删除 |
| `SPRINT_CONTRACT_BUILDER_SYSTEM` | `prompts.py` | 常量删除 |
| `CODE_REVIEWER_SYSTEM` | `prompts.py` | 常量删除 |
| `BROWSER_TESTER_SYSTEM` | `prompts.py` | 常量删除 |
| `EVALUATOR_SYSTEM` | `prompts.py` | 常量删除 |
| `negotiate_contract()` | `harness/sprint.py` | 协商流程删除 |
| `_run_eval_parallel()` | `harness/core.py` | Reviewer 单一 Agent 替代 |
| `SPRINT_CONTRACT_FILE` | `config.py` | 文件合并至 sprint.md |
| `EvalCache.build_evaluator_prompt()` | `eval_cache.py` | 不再需要摘要拼接 |

### 11.3 改动量总结

| 类别 | 新增 | 修改 | 删除 |
|------|------|------|------|
| Prompt 文件 | 4 个 | 1 个（builder 瘦身） | 7 个 |
| Python 常量 | 4 个 | 0 | 7 个 |
| `harness/core.py` | 3 个方法 | 3 个方法 | 1 个方法 |
| `harness/sprint.py` | 0 | 1 个 | 1 个 |
| `config.py` | 2 个常量 | 1 个 | 1 个 |
| `tools/playwright_mcp.py` | 1 个类 + 全局实例 | 2 个函数 | 0 |
| `eval_cache.py` | 0 | 1 个 | 0 |
| `tests/` 目录 | 9 个文件 | 0 | 0 |
| **总计** | **~24 处** | **~10 处** | **~17 处** |

---

## 十二、工具集按 Agent 职能细分

### 12.1 当前设计的盲区

AutoForge 当前只有两层工具划分：

```python
TOOL_SCHEMAS          # 所有 Agent 共享
BROWSER_TOOL_SCHEMAS  # 额外给需要浏览器的 Agent
```

这等于给每个 Agent 发了**同一把万能钥匙**。问题在于：

- **Judge 有 `browser_test` 吗？** 按简化方案 Judge 不该下场测试，但如果 `TOOL_SCHEMAS` 包含 `browser_test`，LLM 完全可能调用——prompt 禁令只是"建议"，不是"硬性阻止"。
- **Architect 有 `run_bash` 吗？** 有。但它的职责是写文档，多给它这个工具，它可能在"研究设计参考"时跑 `npm install` 或 `curl`——这是越界。
- **SprintMaster 有 `generate_image` 吗？** 有。但它不该在规划阶段生成资产。

### 12.2 建议：中等粒度划分

不要做到"每个 Agent 完全独立维护工具集"（维护成本太高），也不要"全共享"（越界风险太高）。按**工具职能类别**分组，按需组合：

```python
# tools/registry.py 或 tools_impl.py

CORE_TOOLS = [          # 所有 Agent 的基础读写
    "read_file",
    "write_file",
    "read_skill_file",
]

FILE_TOOLS = [          # 文件系统操作
    "edit_file",
    "list_files",
]

EXEC_TOOLS = [          # 执行与环境
    "run_bash",
    "start_dev_server",
]

BROWSER_TOOLS = [       # 浏览器测试
    "browser_test",
    "browser_evaluate",
]

GEN_TOOLS = [           # 生成与查询
    "generate_image",
    "search_web",
    "analyze_image",
]

META_TOOLS = [          # 元操作
    "validate_build",
    "project_init",
    "delegate_task",
]
```

然后按 Agent 组合：

| Agent | 工具集 | 理由 |
|-------|--------|------|
| **Architect** | `CORE + GEN + search_web` | 写文档 + 研究设计参考，不需要执行命令或浏览器 |
| **SprintMaster** | `CORE + FILE + list_files` | 读 spec/contract，列出文件，写 sprint.md |
| **Builder** | `CORE + FILE + EXEC + GEN + META` | 全栈开发，需要所有工具 |
| **Reviewer** | `CORE + FILE + BROWSER` | 读代码 + 浏览器测试，不需要生成图片或项目初始化 |
| **Judge** | `CORE`（只保留 `read_file` + `write_file`） | 读报告，写 feedback.md，其他什么都不做 |
| **ComponentBuilder** | `CORE + FILE + BROWSER`（通过 `delegate_task`） | 写单个组件，需要 `browser_evaluate` 验证动画，不需要 `run_bash` 或 `generate_image` |

### 12.3 具体收益

| 越界行为 | 原设计（全共享） | 新设计（职能分组） |
|---------|-----------------|------------------|
| Judge 亲自跑 `browser_test` | ❌ 可能发生 | ✅ `browser_test` 不在 Judge 的工具集中 |
| Architect 执行 `npm install` | ❌ 可能发生 | ✅ `run_bash` 不在 Architect 的工具集中 |
| SprintMaster 调用 `generate_image` | ❌ 可能发生 | ✅ `generate_image` 不在 SprintMaster 的工具集中 |
| Reviewer 调用 `delegate_task` | ❌ 可能发生 | ✅ `delegate_task` 不在 Reviewer 的工具集中 |

### 12.4 实施方式

在 `agents.py` 的 `Agent` 初始化中增加工具集过滤：

```python
class Agent:
    def __init__(self, name, system_prompt, tools, use_state=False, logger=None, allowed_tools=None):
        # ...
        self.tools = tools
        if allowed_tools is not None:
            self.tools = [t for t in tools if t["function"]["name"] in allowed_tools]
```

在 `Harness.__init__` 中实例化时传入：

```python
self.judge = Agent("Judge", JUDGE_SYSTEM, TOOL_SCHEMAS, 
                   allowed_tools={"read_file", "write_file", "read_skill_file"})
```

### 12.5 结论

**值得做，但不要过度细分。** 按 6 个类别分组、5 个 Agent 按需组合，复杂度可控，收益明确。这是用"硬性边界"替代 prompt 里的"软性禁令"，比单纯在 prompt 里写"不要调用 xxx"更可靠。

---

## 十三、上下文压缩策略升级

### 13.1 为什么不能舍弃压缩策略

当前模型上下文窗口确实越来越长（200K+ tokens），但这不意味着可以删除压缩：

| 层面 | 现状 | 结论 |
|------|------|------|
| **技术容量** | 模型支持 200K+ tokens | 确实能装下 |
| **经济成本** | 每轮请求都带完整上下文，token 数 = 费用 | 长上下文 = 高账单 |
| **注意力质量** | LLM 对长序列中"前面部分"的注意力衰减 | 重要信息（spec、system prompt）被稀释 |

**压缩不是"装不下"的问题，是"花不起"和"记不住"的问题。**

### 13.2 当前三级策略的问题

```
> 48K  → State Injection（替换工具返回为摘要）
> 80K  → Compact Messages（LLM 生成摘要）
> 150K → Checkpoint + Reset（生成 handoff 文档，清空）
```

**问题 1：阈值基于"字符数"而非"tokens"**

`COMPRESS_THRESHOLD = 80000` 是字符数。但 LLM 按 token 计费。代码的字符/token 比约 1:0.25，自然语言约 1:0.75。

**改进：** 统一改为 token 计数（用 tiktoken 或字符估算），让阈值和实际成本对齐。

**问题 2：压缩是"被动触发"而非"主动规划"**

当前逻辑：Agent 跑啊跑，突然 `count_tokens(messages) > threshold`，然后紧急压缩。

**改进：** Harness 层在每轮开始前预估"本轮可能消耗多少 tokens"，如果预计会接近阈值，**提前做 State Injection**，而不是等到触发了才做。这样 Builder 一开始就在"瘦身"后的上下文中工作，不会中途被打断。

**问题 3：State Injection 的摘要质量不可控**

当前 `WorkspaceState.summarize()` 是程序化生成（文件列表 + 状态摘要）。随着项目变大，摘要本身也会膨胀。

**改进：** 用 LLM 做 `WorkspaceState` 的压缩摘要。每轮结束后，Harness 层调用一个轻量级摘要请求（只读 WorkspaceState，不读代码），产出 500 字以内的"项目当前状态摘要"，替换掉完整的 State 注入。

### 13.3 更根本的思路：从源头精简

与其事后压缩，不如让 Agent 一开始就在精简的上下文中工作：

#### 1. Spec 和 Contract 的"动态切片"

**当前：** 每轮 Builder 都读完整的 `spec.md` 和 `contract.md`。

**问题：** 如果 spec 有 50 个功能，Builder 本轮只负责其中 2 个，但它仍然载入全部 50 个功能。

**方案：** Harness 层在传入 Builder prompt 前，根据 `sprint.md` 的任务，从 `spec.md` 和 `contract.md` 中提取**相关段落**。

```python
def _slice_spec_for_sprint(spec_path: Path, sprint_path: Path) -> str:
    """根据 sprint 任务，从 spec 中提取相关段落"""
    spec_text = spec_path.read_text()
    sprint_text = sprint_path.read_text()
    
    # 简单启发式：提取 sprint 中提到的功能关键字对应的 spec 段落
    # 更优方案：Architect 在写 spec 时给每个功能打标签
    # SprintMaster 在规划时引用标签，Harness 层按标签切片
    
    return relevant_sections
```

**收益：** Builder 的上下文减少 30-50%，而且信息更聚焦。

#### 2. Feedback 的"未解决项过滤"

**当前：** 每轮 Builder 都读完整的 `feedback.md`。

**问题：** feedback 累积 5 轮后可能有 3000 字，但本轮只关心其中 2 个未修复的 issue。

**方案：** Judge 在写 feedback.md 时，增加 "Resolved / Unresolved" 标记。Harness 层只把 **Unresolved 项** 传入 Builder prompt。

```markdown
# QA Feedback

## Unresolved Issues
- [ ] Issue 3: 移动端导航错位（Round 2 提出，仍未修复）
- [ ] Issue 5: 图片懒加载失效（Round 4 提出）

## Resolved Issues（仅参考，不载入 Builder 上下文）
- [x] Issue 1: 构建失败（Round 1 修复）
```

#### 3. 历史评分的"趋势摘要"

**当前：** Builder 的 prompt 包含 `overall_score_history` 和 `strategy_history` 的完整列表。

**问题：** 第 10 轮的 Builder 不需要看到第 1-9 轮的每次分数。

**方案：** Harness 层把完整历史压缩为趋势摘要：

```markdown
Score Trend: 3.0 → 4.5 → 5.0 → 6.5 → 6.0（最近 5 轮）
Best Round: Round 4 (6.5)
Last Strategy: REFINE
Consecutive REFINEs: 3
```

### 13.4 压缩策略升级总表

| 当前策略 | 升级方向 |
|---------|---------|
| 阈值基于字符数 | 改为基于 token 数（更准确） |
| 被动触发（跑超了再压） | 主动规划（每轮开始前预估并预压缩） |
| State Injection 用程序摘要 | 用 LLM 做 500 字状态摘要 |
| Builder 读完整 spec/contract/feedback | Harness 层切片，只传相关段落 |
| 完整历史分数序列 | 压缩为趋势摘要（5 个数字 + 2 个标签） |

### 13.5 可选增强：上下文预算分配制

给每个 Agent **分配独立的上下文预算**：

```python
AGENT_CONTEXT_BUDGETS = {
    "Architect":       40000,   # 只需要读用户 prompt，写 spec
    "SprintMaster":    60000,   # 读 spec + contract + 文件列表
    "Builder":        120000,   # 核心工作，给最大预算
    "Reviewer":        80000,   # 读代码 + 浏览器测试
    "Judge":           40000,   # 读报告 + 合同，做判断
}
```

这样 Builder 有 120K 的 runway，SprintMaster 只有 60K——强迫 SprintMaster 在更短的上下文里做决策（它本来就不需要那么多）。

### 13.6 结论

**不能删除压缩策略**——窗口变长不等于注意力变强、不等于成本变低。要从"被动应急"升级为"主动规划 + 源头切片"：

1. 阈值从字符数改为 token 数
2. 从"被动触发"改为"主动规划"
3. State Injection 用 LLM 压缩摘要
4. Harness 层按 sprint 任务从 spec/contract 提取相关段落
5. Feedback 只传 Unresolved 项
6. 历史分数压缩为趋势摘要

---

## 执行顺序建议

按以下顺序执行改动，避免在"乱麻上打结"：

1. **Playwright Pool** —— 纯技术优化，无架构风险，立竿见影
2. **5 Agent 简化** + **Builder 瘦身** —— 架构瘦身，减少复杂度
3. **框架 pytest 测试** —— 在简化后的架构上加测试，确保改动没引入回归
4. **动态迭代预算** —— 需要几轮运行积累历史数据
5. **单元测试层 UnitTester** —— 在简化的架构上增加层次
6. **动态轮数限制** —— 最后做，需要系统稳定运行后才能校准参数

---

*本文档基于 AutoForge 现有架构（截至 commit `7eff02f`）编写，涵盖 Agent 架构、Prompt 系统、Harness 编排、配置数据流、性能优化、测试体系、资源控制等全部改动点。*
