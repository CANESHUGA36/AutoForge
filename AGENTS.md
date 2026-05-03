# AutoForge — Agent Guide

> 本文档面向 AI Coding Agent。假设读者对项目一无所知，所有信息均基于实际代码，不做假设。

---

## 1. 项目概述

**AutoForge** 是一个自主多 Agent 开发 Harness，能够根据用户的一句话需求，自动设计、编码、测试并迭代构建完整的 Web 应用。

核心工作流：
1. **Architect** 分析需求，一次性产出 `spec.md`（产品规格）和 `contract.md`（验收标准，按大组组织）
2. **SprintMaster** 根据当前大组规划每轮任务
3. **Builder** 编写代码（前端：Vite/React/TS、Next.js App Router 或纯 HTML）
4. **Reviewer** 自主测试并生成反馈报告
5. **Harness** 循环迭代，直到所有功能组通过或达到轮次上限

项目当前为**纯前端生成器**，路线图（`ROADMAP.md`）规划了向全栈（Next.js + Prisma + PostgreSQL + Auth + 部署）演进的 5 个阶段。

---

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| LLM API | OpenAI-compatible API（可配置 base URL） |
| 浏览器自动化 | Playwright MCP（Model Context Protocol）+ Chrome |
| 前端目标框架 | Vite + React + TypeScript、Next.js App Router、纯 HTML |
| 测试框架 | pytest（单元测试）+ 自定义契约测试框架（静态代码分析） |
| 容器化 | Docker + docker-compose |
| 图像生成 | MiniMax image-01 API（可选） |

---

## 3. 项目结构

```
AutoForge/
├── 根级 Python 模块（核心运行时）
│   ├── run.py              # 入口点：创建 workspace，运行 Harness
│   ├── agents.py           # Agent 类：上下文管理、工具执行、迭代循环
│   ├── config.py           # 环境配置、阈值、超时、路径
│   ├── context.py          # Token 计数、上下文压缩、焦虑检测、检查点
│   ├── dashboard.py        # 实时执行仪表盘（控制台 + 状态跟踪）
│   ├── eval_cache.py       # 每轮 Reviewer 报告持久化
│   ├── prompts.py          # 从 prompts/*.md 懒加载 prompt
│   ├── skills.py           # Skill 目录构建器（读取 skills/*/SKILL.md）
│   ├── tools_impl.py       # 工具实现（文件 I/O、bash、浏览器、构建验证、图像生成）
│   └── workspace_state.py  # 共享 workspace 状态（P2 上下文分层）
│
├── harness/                # Harness 框架
│   ├── core.py             # Harness 编排器：主构建-评估循环（~1800 行）
│   ├── stages.py           # Pipeline 阶段（PreBuild、Build、DevServer、Screenshot、GitCommit）
│   ├── pipeline.py         # 基于阶段的执行引擎，支持自动修复
│   ├── events.py           # 阶段间通信的事件总线
│   ├── eval.py             # 通过率解析、契约交叉验证
│   ├── feature_groups.py   # 大组模式（Big-Group Mode）状态机
│   ├── shared_state.py     # 跨 Agent 知识共享（.shared_state.json）
│   ├── sprint.py           # SprintMaster 规划
│   ├── state.py            # harness_state.json 持久化（原子写入）
│   ├── strategy.py         # Builder 策略解析（REFINE/PIVOT）
│   ├── build.py            # Dev server 验证、端口检测
│   ├── cli.py              # argparse CLI 接口
│   ├── contract_tests.py   # 静态代码分析测试框架（~700 行）
│   ├── framework_adapter.py # Vite/Next.js/纯 HTML 适配器
│   ├── git.py              # GitManager（初始化、提交、回滚）
│   ├── logging.py          # 文件日志设置 + 轮次统计输出
│   └── react_devtools.py   # 通过 CDP 检查 React Fiber 树
│
├── tools/                  # 浏览器自动化桥接
│   └── playwright_mcp.py   # BrowserSessionPool + browser_check()（~1000 行）
│
├── prompts/                # Agent system prompts（中文）
│   ├── architect.md        # 产品规格 + 契约生成
│   ├── builder.md          # 编码规则（防御性编码、CSS 显隐、禁止条件渲染）
│   ├── reviewer.md         # 测试策略（代码审查 > 浏览器测试）
│   └── sprint_master.md    # 基于契约大组的 Sprint 规划
│
├── skills/                 # 16 个 skill 目录，含参考指南
│   ├── a11y-checklist/
│   ├── animation-patterns/
│   ├── browser-testing/
│   ├── build-troubleshooting/
│   ├── builder-patterns/
│   ├── component-testing/
│   ├── contract-testing/
│   ├── dev-server-management/
│   ├── frontend-design/
│   ├── image-generation/
│   ├── nextjs-app-router/
│   ├── react-devtools/
│   ├── react-ecosystem/
│   ├── reviewer-patterns/
│   ├── state-persistence/
│   └── vite-cache/
│
├── tests/                  # pytest 测试套件（16 个测试文件）
│   ├── conftest.py         # mock_workspace fixture（含 git init）
│   ├── test_agents.py
│   ├── test_context.py
│   ├── test_css_validation.py
│   ├── test_eval.py
│   ├── test_eval_cache.py
│   ├── test_feature_groups.py
│   ├── test_harness_core.py
│   ├── test_harness_core_extra.py
│   ├── test_new_tools.py
│   ├── test_pipeline.py
│   ├── test_stages.py
│   ├── test_state.py
│   ├── test_strategy.py
│   ├── test_tools_impl.py
│   ├── test_tools_impl_extra.py
│   └── test_workspace_state.py
│
└── docs/                   # （当前为空）
```

---

## 4. 构建与运行命令

### 本地运行

```bash
# 运行 Harness（使用用户提示）
python run.py "Build a personal finance dashboard"

# 或使用 CLI（支持更多参数）
python -m harness.cli "Build a pomodoro timer"

# 指定 workspace（用于恢复中断的运行）
python -m harness.cli "Build a pomodoro timer" --workspace ./projects/my-app-20260101-120000

# 强制重置（删除状态文件后重新运行）
python -m harness.cli "Build a pomodoro timer" --workspace ./projects/my-app --reset

# 查看仪表盘状态
python -m harness.cli --workspace ./projects/my-app --dashboard
```

### 环境变量（.env）

必需：
- `OPENAI_API_KEY` — LLM API 密钥
- `OPENAI_BASE_URL` — API base URL（默认 https://api.openai.com/v1）
- `HARNESS_MODEL` — 模型名称（默认 gpt-4o）

可选：
- `HARNESS_WORKSPACE` — 恢复已有 workspace
- `HARNESS_PROJECTS_DIR` — 自动生成 workspace 的父目录（默认 ./projects）
- `MAX_HARNESS_ROUNDS` — 最大轮次（0 = 动态计算）
- `GROUP_PASS_THRESHOLD` — 大组通过阈值（默认 0.70）
- `OVERALL_PASS_THRESHOLD` — 全局通过阈值（默认 0.75）
- `CONTRACT_TEST_ENABLED` / `REACT_DEVTOOLS_ENABLED` — 分层验证开关

### Docker 运行

```bash
# 启动服务（使用 .env 文件）
docker compose up autoforge

# 覆盖命令运行特定提示
docker compose run autoforge python run.py "Build a todo app"
```

### 测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_harness_core.py

# 遇到第一个失败即停止
pytest -x
```

---

## 5. 代码组织与模块划分

### 5.1 Agent 系统（agents.py）

`Agent` 类是核心抽象：
- 每个 Agent 有独立的 `system_prompt`、`tools` 和 `allowed_tools`
- 支持上下文生命周期管理（压缩、状态注入、检查点重置）
- 支持 `use_state` 模式（WorkspaceState 分层）
- 结构化日志输出到 `.events/{agent_name}.jsonl`

当前 4 个 Agent 及其工具权限：

| Agent | 核心工具 | 文件工具 | 执行工具 | 浏览器工具 | 生成工具 | 元工具 |
|-------|---------|---------|---------|-----------|---------|-------|
| Architect | read_file, write_file, read_skill_file | — | — | — | generate_image, search_web, analyze_image | — |
| SprintMaster | read_file, write_file, read_skill_file | edit_file, list_files | — | — | — | — |
| Builder | read_file, write_file, read_skill_file | edit_file, list_files | run_bash | — | generate_image, search_web, analyze_image | validate_build, project_init |
| Reviewer | read_file, write_file, read_skill_file | edit_file, list_files | — | browser_check, contract_test_run, react_devtools_inspect, check_console_logs, detect_framework, run_diagnostics, check_responsive, check_a11y, check_performance, check_routes, mock_api | — | — |

### 5.2 Harness 核心（harness/core.py）

`Harness` 类编排完整流程：
1. **Phase 1（设计）**：Architect 生成 spec.md + contract.md
2. **Phase 2+（构建-评估循环）**：每轮执行 Pipeline（Builder → BuildGate → DevServerGate → ScreenshotGate → GitCommit → Reviewer）
3. **退出条件**：基于大组通过率（`GROUP_PASS_THRESHOLD = 0.70`）和全局通过率（`OVERALL_PASS_THRESHOLD = 0.75`）
4. **Final Review**：通过后运行全量回归检查

### 5.3 Pipeline 架构（harness/pipeline.py + stages.py）

`PipelineRunner` 按顺序执行阶段：
- `PreBuildGateStage` — 环境检查（node_modules、构建工具、构建通过）
- `BuildGateStage` — Builder 后构建验证
- `DevServerGateStage` — Dev server 启动与健康检查
- `ScreenshotGateStage` — 截图保存
- `GitCommitStage` — 自动 git commit

阶段支持 `auto_fix`（如缺失 node_modules 时自动 `npm install`）。

### 5.4 大组模式（harness/feature_groups.py）

契约标准按大组（Group 1, Group 2, ...）组织，每组包含子功能（G1.A, G1.B, ...）。

- 每轮只推进一个大组
- 大组通过条件：通过率 ≥ `GROUP_PASS_THRESHOLD`（默认 70%）且无 `CRITICAL_BUG`
- 连续 3 轮未通过视为卡死（stuck），提示 Builder 改变策略

### 5.5 上下文管理（context.py + workspace_state.py）

三层策略（针对 200K 上下文模型）：
1. **压缩**（180K tokens）：LLM 生成历史摘要
2. **状态注入**（108K tokens）：用 WorkspaceState 摘要替代工具返回
3. **检查点重置**（200K tokens 或焦虑检测）：保存检查点后重置上下文

### 5.6 浏览器自动化（tools/playwright_mcp.py）

`BrowserSessionPool` 单例管理 Playwright MCP 会话：
- 支持多 viewport（desktop、mobile）
- 统一缓存管理（Vite server cache、HTTP cache、Service Worker）
- `browser_check()` 为统一入口，支持 inspect / interact / screenshot 模式

---

## 6. 开发规范

### 6.1 语言与注释

- **Prompt 和主要注释使用中文**
- 代码中的日志输出混合中英文（关键指标用英文，解释用中文）

### 6.2 Builder 防御性编码规则（强制）

**规则 1：永远用 CSS 控制显隐，绝对禁止条件渲染**

条件渲染（`{condition && <Element />}`）会导致契约测试和浏览器测试找不到元素，是最常见的失败原因。

```tsx
// ❌ 禁止
{audioUrl && <div className="spectrum-container">...</div>}

// ✅ 正确
<div className="spectrum-container" style={{display: audioUrl ? 'flex' : 'none'}}>
  ...
</div>
```

**规则 2：所有可测试元素必须带 `data-testid`**

格式：`{功能组}-{标准号}-{元素名}`，如 `data-testid="f1-waveform-canvas"`

### 6.3 工具调用安全

- 每个 Agent 有 `allowed_tools` 集合，LLM 调用未授权工具会被拒绝
- Builder 有环境修复预算：连续 5 次 npm/install/tsc 类工具调用会强制 PIVOT
- 文件路径通过 `_resolve()` 检查，禁止逃出 workspace

### 6.4 Git 检查点

- 每轮结束后自动 `git commit`
- 契约通过率下降超过 10% 时自动回滚到最佳轮次
- 纯 HTML 项目同样初始化 git 仓库

---

## 7. 测试策略

### 7.1 测试类型

| 测试类型 | 实现位置 | 说明 |
|---------|---------|------|
| 单元测试 | `tests/` | pytest，使用 mock OpenAI client 和 tmp_path fixtures |
| 契约测试 | `harness/contract_tests.py` | 静态代码分析：检查组件导出、JSX、事件处理、状态管理 |
| React DevTools | `harness/react_devtools.py` | 通过 CDP 检查 Fiber 树中的组件存在性和 props |
| 浏览器测试 | `tools/playwright_mcp.py` | DOM 检查、点击/填写交互、截图 |
| 构建验证 | `tools_impl.py::validate_build()` | `npm run build` 退出码检查 + stderr 启发式回退 |
| 交叉验证 | `harness/eval.py` | Reviewer 报告 vs contract.md 真实标准数对比 |

### 7.2 核心测试原则

**代码审查 > 浏览器测试**

React 受控组件无法被程序化触发（如点击不触发 onClick），因此代码正确性是主要证据。Reviewer 应优先使用 `read_file` 和 `contract_test_run`，限制 `browser_check` 调用次数（最多 3 次）。

### 7.3 运行测试

```bash
pytest                    # 全部测试
pytest tests/test_harness_core.py -v   # 详细输出
pytest -x                # 第一个失败即停止
```

---

## 8. 部署

### 8.1 Docker（主要方式）

- 基础镜像：`autoforge:v8`（预装 Python 3.12 + Playwright + Chrome）
- 源码通过 volume 挂载实现实时编辑
- 项目通过 `./projects:/projects` volume 持久化
- 环境变量从 `.env` 文件加载

### 8.2 本地

- `python run.py "<prompt>"` 在 `./projects/` 下创建带时间戳的 workspace
- 需要 `.env` 文件配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`HARNESS_MODEL`

### 8.3 恢复机制

- 设置 `HARNESS_WORKSPACE=./projects/my-app-20260101-120000` 可从 `harness_state.json` 恢复
- 恢复时跳过 Phase 1（spec/contract 已存在），从上次完成的轮次继续

---

## 9. 配置参考

关键配置项（`config.py`）：

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `COMPRESS_THRESHOLD` | 180000 | 上下文压缩触发阈值（tokens） |
| `RESET_THRESHOLD` | 200000 | 上下文重置触发阈值（tokens） |
| `MAX_ROUNDS_HARD` | 20 | 绝对最大轮次 |
| `GROUP_PASS_THRESHOLD` | 0.70 | 单个大组通过阈值 |
| `OVERALL_PASS_THRESHOLD` | 0.75 | 全局通过阈值 |
| `MAX_ITERATIONS_BUILDER` | 80 | Builder 每轮最大迭代数 |
| `MAX_ITERATIONS_REVIEWER` | 50 | Reviewer 每轮最大迭代数 |
| `DEV_SERVER_PORTS` | vite: 5173, nextjs: 3000 | 各框架 dev server 端口 |
| `TIMEOUT_BUILD` | 180 | 构建超时（秒） |
| `TIMEOUT_BROWSER_TEST` | 120 | 浏览器测试超时（秒） |

---

## 10. 安全注意事项

1. **路径逃逸防护**：`tools_impl.py` 中 `_resolve()` 检查文件路径是否以 workspace 路径开头
2. **SQL 注入防护**：`db_query` 工具（路线图中）默认只读模式，拒绝 DROP/DELETE/UPDATE/INSERT
3. **敏感信息**：`.env` 文件包含 API 密钥，不应提交到 git（已包含在 `.gitignore` 中）
4. **子进程安全**：所有 `run_bash` 调用有超时保护，使用 `subprocess.run` 的 `timeout` 参数
5. **浏览器隔离**：每轮结束后关闭所有 browser session，防止 Chrome 进程累积

---

## 11. 扩展项目时的关键文件

| 扩展方向 | 需要修改的文件 |
|---------|--------------|
| 新增工具 | `tools_impl.py`（注册 schema + 实现函数） |
| 新增 Agent | `agents.py` + `harness/core.py` + `prompts/` |
| 新增 Pipeline 阶段 | `harness/stages.py` + `harness/core.py` |
| 新增框架适配器 | `harness/framework_adapter.py` |
| 新增 Skill | `skills/{name}/SKILL.md` |
| 修改 Agent 行为 | `prompts/{agent}.md` |
| 修改评分逻辑 | `harness/eval.py` + `harness/feature_groups.py` |
| 修改上下文策略 | `context.py` + `workspace_state.py` |
