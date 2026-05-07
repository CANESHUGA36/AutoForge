# AutoForge

AutoForge 是一个自动化前端项目构建框架。给定一段自然语言描述，它能够从零开始规划、编码、测试并交付一个可运行的 Web 应用。

## 核心特性

- **多 Agent 协作**：Architect 设计架构，SprintMaster 规划任务，Builder 编写代码，Reviewer 评审质量
- **分轮次迭代**：按功能组（Feature Group）逐轮构建，每轮包含完整的 Build-Evaluate 循环
- **自动化流水线**：环境检查 → 构建 → 设计检查 → 开发服务器验证 → 截图 → Git 提交
- **设计规则引擎**：内置 D1-D7 设计约束检查（Tailwind 一致性、Lucide 图标、空状态等）
- **契约测试**：基于 `data-testid` 的静态代码分析，无需浏览器即可验证功能
- **DeepSeek 适配**：完整适配 DeepSeek 1M 上下文，支持 reasoning_content 回传

## 架构概览

```
用户 Prompt
    │
    ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Architect  │───→│ SprintMaster│───→│   Builder   │───→│  Reviewer   │
│  (架构设计)  │    │  (任务规划)  │    │  (代码编写)  │    │  (质量评审)  │
└─────────────┘    └─────────────┘    └──────┬──────┘    └──────┬──────┘
                                              │                    │
                                              ▼                    │
                                    ┌──────────────────┐          │
                                    │     Pipeline     │          │
                                    │  · BuildGate     │          │
                                    │  · DesignLint    │          │
                                    │  · DevServerGate │          │
                                    │  · Screenshot    │          │
                                    │  · GitCommit     │          │
                                    └──────────────────┘          │
                                              │                    │
                                              └──────────┬─────────┘
                                                         ▼
                                              ┌──────────────────┐
                                              │  EvalCache       │
                                              │  (feedback.md)   │
                                              └──────────────────┘
                                                         │
                                    通过 ─────────────────┘
                                    未通过 ───────────────→ 下一轮 Builder
```

## 快速开始

### 1. 准备环境

创建 `.env` 文件：

```env
# 主 LLM（DeepSeek）
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
HARNESS_MODEL=deepseek-chat

# 工具 LLM（MiniMax：搜索/图片生成）
MINIMAX_API_KEY=xxxxxxxx
MINIMAX_BASE_URL=https://api.minimaxi.com
```

### 2. 构建镜像

```bash
docker build -t autoforge:v8 .
```

### 3. 运行项目

```bash
docker compose run autoforge python run.py "你的项目描述"
```

或恢复已有项目：

```bash
docker compose run -e HARNESS_WORKSPACE=/projects/项目目录 autoforge python run.py "你的项目描述"
```

### 4. 查看结果

构建完成后，项目代码位于 `./projects/` 目录下。

## Agent 职责

| Agent | 职责 | 输出 |
|-------|------|------|
| **Architect** | 分析需求、设计产品架构、制定技术方案 | `spec.md`, `contract.md` |
| **SprintMaster** | 读取 contract，按功能组拆分任务，规划每轮 sprint | `sprint.md` |
| **Builder** | 根据 sprint.md 编写代码，支持自动构建验证 | `src/*` |
| **Reviewer** | 代码审查、设计一致性检查、回归测试 | `feedback.md` |

## Pipeline 阶段

每轮 Builder 完成后，自动执行以下流水线：

| 阶段 | 说明 | 是否阻塞 |
|------|------|----------|
| **PreBuildGate** | 环境检查、依赖修复 | 是 |
| **BuildGate** | `npm run build` 生产构建 | 是 |
| **DesignLint** | Tailwind 类名一致性、Lucide 图标、空状态、圆角一致性 | 否（警告） |
| **DevServerGate** | 开发服务器可访问性检查 | 是 |
| **ScreenshotGate** | 页面截图存档 | 否 |
| **GitCommitStage** | 自动 git commit | 否 |

## 设计约束（D1-D7）

Builder 必须遵守以下设计规则：

- **D1 配色**：使用 Tailwind 标准色（`slate`, `gray`, `blue`），禁止任意值颜色类（`text-[#1a1a1a]`）
- **D2 圆角与阴影**：所有按钮使用 `rounded-lg shadow-sm`
- **D3 Hover 反馈**：交互元素必须有明确的 hover/active 状态
- **D4 空状态**：空列表/面板必须使用 `InboxIcon` + 引导文字，禁止纯文本"暂无数据"
- **D5 图标库**：所有图标必须使用 `lucide-react`，禁止自定义 SVG
- **D6 间距**：使用 Tailwind 标准间距（`p-2`, `p-3`, `gap-4` 等 4px 倍数）
- **D7 文字层级**：标题 `font-semibold`，正文 `font-normal`，辅助文字 `text-sm text-gray-500`

**渲染约束**：所有条件渲染必须使用 CSS `display` 属性控制，禁止 `{condition && <Element />}`。

## 项目结构

```
AutoForge/
│
├─ 入口与配置
│   ├── run.py              # 主入口：解析参数、创建/恢复 workspace、启动 Harness
│   ├── config.py           # 全局配置：模型参数、阈值、迭代限制、MiniMax 双 key
│   ├── docker-compose.yml  # Docker 编排：volume 挂载、环境变量、服务定义
│   └── Dockerfile          # 镜像定义：Python 3.13 + Node.js + Playwright
│
├─ Agent 层（LLM 对话引擎）
│   ├── agents.py           # Agent 基类：对话循环、tool calling、token 追踪、上下文管理
│   ├── context.py          # 上下文压缩：滑动窗口、reasoning_content 处理（DeepSeek 适配）
│   ├── prompts.py          # 系统提示词加载器：读取 prompts/ 目录并注入变量
│   └── dashboard.py        # 实时看板：每轮迭代进度、token 消耗、构建状态
│
├─ 工具层（Agent 可调用的能力）
│   ├── tools_impl.py       # 核心工具：文件读写、edit_file、run_bash、validate_build、截图
│   ├── tools/
│   │   └── playwright_mcp.py   # Playwright MCP 封装：浏览器操作、截图、元素检查
│   └── skills.py           # 技能加载器：按需读取 skills/ 目录给 Agent
│
├─ 状态与缓存
│   ├── workspace_state.py  # 代码索引：文件结构、landmark、总行数、构建状态
│   ├── eval_cache.py       # 评审缓存：feedback.md 解析、历史结果存储
│   └── workspace/          # 临时工作区：当前运行的项目文件（会被 projects/ 替换）
│
├─ Harness（编排引擎）
│   ├── harness/
│   │   ├── core.py             # Harness 主控：_build_round()、_run_evaluation()、Final Review
│   │   ├── pipeline.py         # PipelineRunner：顺序执行各 Stage
│   │   ├── stages.py           # Stage 实现：BuildGate、DesignLint、DevServerGate、Screenshot、GitCommit
│   │   ├── feature_groups.py   # 功能组解析：contract.md → 分组、状态追踪、退出条件
│   │   ├── sprint.py           # Sprint 管理：sprint.md 读写、状态流转
│   │   ├── eval.py             # 评审解析：feedback.md → pass rate、contract rate
│   │   ├── contract_tests.py   # 静态契约测试：基于 data-testid 的代码分析
│   │   ├── framework_adapter.py# 框架适配：Vite/Next.js 项目结构识别
│   │   ├── git.py              # Git 管理：自动 commit、diff 生成
│   │   ├── react_devtools.py   # React DevTools 检查：组件树验证
│   │   ├── build.py            # 构建封装：npm build 调用与结果解析
│   │   ├── cli.py              # CLI 工具：命令行辅助
│   │   ├── events.py           # 事件总线：Stage 间通信
│   │   ├── logging.py          # 日志配置：文件日志、格式化
│   │   └── shared_state.py     # 跨 Agent 共享状态：tech_stack、constraints、架构决策
│
├─ 提示词（可热更新）
│   └── prompts/
│       ├── architect.md        # Architect 系统提示词：架构设计、spec/contract 规范
│       ├── builder.md          # Builder 系统提示词：编码规则、D1-D7、自检命令
│       ├── reviewer.md         # Reviewer 系统提示词：代码审查、设计一致性、回归检查
│       └── sprint_master.md    # SprintMaster 系统提示词：任务拆分、依赖分析
│
├─ 技能库（Agent 可读取的知识）
│   └── skills/
│       ├── builder-patterns/       # Builder 反模式与最佳实践
│       ├── contract-testing/       # 契约测试指南
│       ├── frontend-design/        # 前端设计规范、Tailwind 用法
│       ├── react-ecosystem/        # React + TypeScript + Tailwind 最佳实践
│       ├── build-troubleshooting/  # 常见构建错误排查
│       ├── dev-server-management/  # 开发服务器管理
│       ├── image-generation/       # 图片生成工具使用指南
│       └── ...
│
├─ 测试
│   └── tests/
│       ├── test_agents.py
│       ├── test_harness_core.py
│       ├── test_pipeline.py
│       ├── test_stages.py
│       ├── test_tools_impl.py
│       └── ...
│
└─ 输出目录（运行时生成）
    └── projects/                 # 所有生成的项目存放于此
        └── build-a-xxx-YYYYMMDD-HHMMSS/
            ├── src/              # 项目源码（Vite/React/TypeScript）
            ├── spec.md           # Architect 输出的产品规格
            ├── contract.md       # 验收标准（按功能组分组）
            ├── sprint.md         # 当前轮次任务规划
            ├── feedback.md       # Reviewer 输出的评审结果
            ├── harness.log       # 完整构建日志
            ├── harness_state.json# 恢复状态（中断后可续跑）
            └── .eval_cache/      # 各轮次评审缓存
```

## 配置说明

### config.py 关键参数

```python
COMPRESS_THRESHOLD = 600000   # 上下文压缩阈值（tokens）
RESET_THRESHOLD = 900000      # 上下文重置阈值（tokens）
AGENT_ITERATION_LIMITS = {
    "builder": 120,           # Builder 最大迭代次数
    "reviewer": 80,           # Reviewer 最大迭代次数
}
```

### docker-compose.yml

```yaml
services:
  autoforge:
    image: autoforge:v8
    env_file: .env
    environment:
      HARNESS_PROJECTS_DIR: /projects
      CONTRACT_TEST_ENABLED: "true"
    volumes:
      - ./projects:/projects
      - ./prompts:/app/prompts:ro
      - ./skills:/app/skills:ro
      - ./harness:/app/harness:ro
      - ./config.py:/app/config.py:ro
      - ./agents.py:/app/agents.py:ro
```

所有框架代码通过 volume 挂载，**修改后无需重建镜像即可生效**。

## 开发指南

### 修改提示词

直接编辑 `prompts/` 目录下的 `.md` 文件，容器内会实时加载。

### 添加 Pipeline 阶段

在 `harness/stages.py` 中继承 `PipelineStage`：

```python
class MyStage(PipelineStage):
    name = "my_stage"
    def run(self) -> StageResult:
        # 你的检查逻辑
        return StageResult(success=True, message="OK")
```

然后在 `harness/core.py` 的 `_build_round()` 中注册：

```python
runner.add_stage(MyStage)
```

### 调试

```bash
# 查看容器日志
docker logs --tail 100 autoforge-resume

# 进入容器调试
docker exec -it autoforge-resume bash
```

## 常见问题

**Q: 容器启动后立即退出？**
A: 检查 `.env` 中 API key 是否配置正确。容器需要有效的 LLM 配置才能启动。

**Q: Builder 频繁触发构建失败？**
A: 检查 `tools_impl.py` 中的 `_BUILD_CHECK_COOLDOWN`，默认 3 秒防抖。过短的 cooldown 会导致不必要的构建。

**Q: 如何恢复已中断的项目？**
A: 使用 `HARNESS_WORKSPACE` 环境变量指向已有项目目录，Harness 会自动从 `harness_state.json` 恢复状态。

## License

MIT
