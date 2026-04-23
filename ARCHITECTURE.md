# AutoForge 架构梳理 —— 由内向外

> 本文档按依赖关系从内到外梳理，每一层都标注其承担的**工程控制职责**（并发、状态、容错、预算等）。

---

## 第0层：入口点 (`run.py`)

```
用户命令 → run.py → Harness.run(prompt)
```

**工程控制：**
- **Windows UTF-8 强制**：在 `import` 任何模块之前，`sys.stdout/stderr` 被重绑定为 `io.TextIOWrapper(..., encoding="utf-8")`，防止 GBK 编码错误污染日志。
- **Workspace 隔离**：基于 prompt 生成 `safe-name-{timestamp}` 目录，所有产物写入该目录，避免跨运行污染。

---

## 第1层：Harness 核心编排 (`harness/core.py`)

这是整个系统的**心脏**。一个 `Harness` 实例 = 一个完整的项目构建生命周期。

### 1.1 核心流程

```
Plan (spec.md)
    ↓
Contract (contract.md + sprint_contract.md)
    ↓
Round 1..N: Build → Evaluate → Score
    ↓
Pass? → 结束 / Fail? → 继续下一轮 (最多 MAX_ROUNDS)
```

### 1.2 工程控制 —— 状态持久化与恢复

**位置：`Harness.__init__` + `_save_state` / `_load_state` / `_clear_state`**

- **`harness_state.json`**：每轮结束后原子写入（`StateManager` 用 `.tmp` + `replace` 保证原子性）。
- **恢复语义**：如果进程崩溃/被 kill，重新运行 `run.py` 会检测到 `harness_state.json` 并从中恢复 `completed_rounds`、`score_history`、`token_totals`、`strategy_history`。
- **清理语义**：成功完成后 `_clear_state()` 删除状态文件，防止下次误恢复。

### 1.3 工程控制 —— Git 快照与回滚

**位置：`_build_round` 中的 rollback 逻辑 + `harness/git.py`**

- **每轮强制快照**：Build 结束后调用 `git.commit_round(round_num)`，消息格式 `"harness: round N snapshot"`。
- **双条件回滚**：
  1. **Sprint 分低于门槛** (`SPRINT_PASS_THRESHOLD=6.0`)：回滚到 `HEAD`，强制 Builder 修复当前 sprint 的失败项。
  2. **Overall 分显著下降** (`SIGNIFICANT_DROP=1.0`)：回滚到历史最佳轮次的 commit hash（通过 `git log --grep "round X snapshot"` 动态查找）。
- **硬重置**：使用 `git reset --hard`，不保留 working tree 脏状态。

### 1.4 工程控制 —— Build Gate

**位置：`_build_round` 中 "Build 验证跳过评估" 的分支**

- Build 完成后读取 `.workspace_state.json` 的 `last_build_status`。
- 如果为 `"error"`，**直接跳过整个 Evaluate 阶段**，赋予惩罚分 (`SPRINT_PASS_THRESHOLD - 0.5`)，不浪费评估 token。
- 同时 `verify_dev_server()` 在 Harness 层做 HTTP 健康检查（轮询 30s），如果失败记录 dashboard alert 但**不阻塞**流程。

### 1.5 工程控制 —— 并行评估

**位置：`_run_eval_parallel`**

- `ThreadPoolExecutor(max_workers=2)` 同时运行 `CodeReviewer` 和 `BrowserTester`。
- **每个 Agent 5 分钟超时** (`EVAL_TIMEOUT=300`)：超时后返回降级结果，不阻塞 Evaluator。
- **异常隔离**：CodeReviewer crash 不影响 BrowserTester，反之亦然。

### 1.6 工程控制 —— 维度硬门槛

**位置：`_build_round` 末尾 + `harness/eval.py`**

- Evaluator 输出被解析为 4 个维度分：`functionality`、`design_quality`、`originality`、`craft`。
- 任何维度低于 `DIMENSION_THRESHOLDS`（如 functionality < 5.0），即使 overall 分 ≥ 7.0，也会被**强制 cap 到 `PASS_THRESHOLD - 0.1`**。
- 防止"高分但核心功能缺失"的 false positive。

### 1.7 工程控制 —— 策略解析

**位置：`harness/strategy.py` + `_build_round` 中的 `parse_strategy`**

- Builder 每轮必须在输出中包含 `STRATEGY: REFINE|PIVOT` 和 `REASON: ...`。
- Harness 提取策略并注入下一轮 prompt：
  - `PIVOT` → 强制要求"从头开始，删除核心文件重建"。
  - `REFINE` → 继续改进现有实现。
- 避免 Builder 在死胡同里无限修补。

---

## 第2层：Agent 运行时 (`agents.py`)

Agent = 一个 LLM + System Prompt + Tools 的循环执行器。

### 2.1 核心循环

```
for iteration in 1..MAX_ITERATIONS:
    1. 检查上下文生命周期
    2. LLM chat.completions.create(tools=...)
    3. 如果有 tool_calls → 逐个执行 → 将结果 append 到 messages
    4. 如果没有 tool_calls → 返回最终文本
```

### 2.2 工程控制 —— 并发执行

**位置：`_AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=4)`**

- `Agent.run_async()` 将同步的 `run_with_stats()` 提交到线程池。
- Harness 并行调用多个 Agent 时使用（如 CodeReviewer ∥ BrowserTester）。
- 线程隔离：每个 Agent 在自己的线程中运行，独立的 `messages` 列表。

### 2.3 工程控制 —— 上下文生命周期管理

**位置：`Agent._check_context_lifecycle`**

三层递进策略，按 token 数触发：

| 阈值 | 策略 | 动作 |
|------|------|------|
| > `COMPRESS_THRESHOLD * 0.6` (48K) | **State Injection** | 将历史 tool returns 替换为 `WorkspaceState.summarize()` 摘要 |
| > `COMPRESS_THRESHOLD` (80K) | **消息压缩** | 调用 LLM 生成历史摘要，保留最近 N 条消息 |
| > `RESET_THRESHOLD` (150K) | **Checkpoint + 重置** | 生成 handoff 文档写入 `progress.md`，清空 messages，只保留 system prompt + handoff |

- **焦虑检测**：`context.detect_anxiety()` 用正则匹配 LLM 输出中的"let me wrap up"、"running low on tokens"等信号，提前触发 checkpoint。
- **安全分割**：`_safe_split_index()` 确保压缩不会在 tool/assistant message 之间切断（避免 broken tool_call chain）。

### 2.4 工程控制 —— Token 预算与超时

- **Agent 级时间限制**：`AGENT_TIME_LIMIT_S = 3600`，单 Agent 运行超过 1 小时强制终止。
- **Token 统计**：`AgentRunLog` 实时累加 `prompt_tokens` + `completion_tokens`，每轮输出到日志。
- **Max Iterations**：默认 80 轮，防止无限循环。

### 2.5 工程控制 —— 结构化日志

**位置：`AgentRunLog`**

- 每次 tool call 记录：`name`、`status`（ok/error）、`latency_ms`、`result_len`。
- Agent 结束时输出 JSON 行：
  ```json
  {"agent":"BrowserTester","elapsed_s":201.7,"iterations":23,
   "prompt_tokens":65671,"completion_tokens":4567,"status":"success",
   "tool_summary":[...]}
  ```
- 同时写入 `.events/{agent_name}.jsonl`，供外部监控消费。

---

## 第3层：工具执行层

### 3.1 工具分发 (`tools_impl.py` + `execute_tool`)

```
Agent → execute_tool(name, arguments) → TOOL_DISPATCH[name](**arguments)
```

**工程控制：**
- **统一错误格式**：所有工具异常被捕获，返回 `"[error] {msg}"` 字符串。Agent 通过前缀判断失败。
- **路径沙箱**：`_resolve(path)` 将相对路径解析为 `Path(config.WORKSPACE, path).resolve()`，并检查 `str(p).startswith(str(ws))`，**防止路径逃逸攻击**。

### 3.2 文件操作工具

**工程控制：**
- `read_file`：30KB 截断，超出部分标注 `[TRUNCATED]`。
- `write_file`：**写后自动触发 debounced build 验证**（`_auto_validate_build`）。30 秒冷却期，只对 `.tsx/.ts/.jsx/.js/.css/.scss` 文件触发。
- `edit_file`：
  - 精确字符串匹配（`old_string in content`）。
  - 多重匹配检测：如果 `old_string` 出现多次，拒绝编辑并提示"Add more context"。
  - 不匹配时提供 partial match hint（显示前 40 字符匹配的行号）。
- `list_files`：
  - 10K 条目截断。
  - **mtime 缓存**：`_FILE_LIST_CACHE` 按目录 mtime 缓存结果，避免反复扫描 `node_modules`。
  - 默认排除 `.git`, `node_modules`, `.next`, `dist`, `__pycache__` 等。

### 3.3 Bash 执行 (`run_bash`)

**工程控制：**
- **超时控制**：默认 900s，`create-next-app` 类命令自动延长至 600s。
- **后台进程检测**：命令包含 `&` 时自动 detached（`start_new_session=True` / `CREATE_NEW_PROCESS_GROUP`）。
- **跨平台信号**：Windows 用 `CTRL_BREAK_EVENT`，Unix 用 `proc.terminate()` → `proc.kill()`。
- **智能截断** (`_smart_truncate`)：
  - 总预算 10K 字符。
  - stderr 优先保留（预算的 40%）。
  - stdout 采用 head(40%) + middle(关键错误行提取) + tail(40%) 结构。
  - 中间部分通过正则提取含 `error|fail|exception|traceback` 的行。

### 3.4 Dev Server 管理 (`start_dev_server`)

**工程控制：**
- **端口抢占清理**：启动前调用 `_kill_port(port)`（Windows: `netstat` + `taskkill`；Unix: `fuser`/`lsof` + `kill -9`）。
- **Next.js 缓存清理**：自动删除 `.next` 目录。
- **Build 预检**：启动前先跑 `npm run build`，如果失败直接返回 `[BUILD ERROR]`，不启动 server。
- **进程跟踪**：`_server_pids` 字典记录 port→pid，健康检查确认 HTTP 200 后才返回成功。

### 3.5 浏览器测试 (`tools/playwright_mcp.py`)

**工程控制：**
- **Per-call 生命周期**：每次 `browser_test_mcp()` / `browser_evaluate_mcp()` 创建全新的 `PlaywrightMCPBridge`，在 `asyncio.run()` 的 `finally` 中关闭，**避免跨调用复用 async generator 状态**。
- **脚本包装** (`_wrap_script`)：裸 JS 表达式自动包装为 `() => { return expr; }`，`await` 脚本自动包装为 `async () => { ... }`，解决 Playwright MCP "not well-serializable" 错误。
- **清理对称性**：`close()` 使用 `__aexit__()`（与 `__aenter__()` 对称）替代 `aclose()`，缓解 anyio CancelScope race condition。
- **结果截断**：`_smart_truncate_browser_result` 4000 字符预算，保留 viewport、导航、错误、截图、JS eval 关键行。

### 3.6 构建验证 (`validate_build`)

**工程控制：**
- 显式调用时运行 `npm run build`，通过启发式规则判断真实错误（排除 "0 errors"、"compiled successfully" 等 false positive）。
- 更新 `WorkspaceState.last_build_status`，供 Harness Build Gate 消费。

---

## 第4层：上下文与状态管理

### 4.1 WorkspaceState (`workspace_state.py`)

**设计目标**：用结构化状态替代 messages 中的工具返回，让 Agent 上下文增长从 **O(代码量)** 降到 **O(操作次数)**。

**工程控制：**
- **增量更新**：`update_from_tool_result()` 根据工具名和结果推断状态变化：
  - `write_file` / `edit_file` → 更新文件元数据（size, lines, summary）
  - `run_bash` → 检测 `npm install`/`npm run build`/`git commit`/`git status`，更新依赖/构建/Git 状态
  - `browser_test` → 更新 `last_test_status`
  - `list_files` → 检测文件删除
- **分层状态**：L0(系统提示) + L1(State 摘要) + L2(最近 N 轮) + L3(压缩历史)。
- **持久化**：`.workspace_state.json`，Agent 每次 tool call 后自动 `save()`。

### 4.2 EvalCache (`eval_cache.py`)

**工程控制：**
- **完整报告落盘**：每轮 CodeReview + BrowserTest 的完整文本保存到 `.eval_cache/round_N_{code_review,browser}.md`。
- **结构化摘要提取**：通过正则从报告中提取 `critical_count`、`warning_count`、`coverage`、`desktop_status` 等字段。
- **Evaluator Prompt 构建**：Evaluator 不接收完整报告，而是接收 `<1500 字符的结构化摘要` + 最近 2 轮历史趋势，大幅降低上下文长度。

### 4.3 上下文压缩 (`context.py`)

**工程控制：**
- **Token 计数**：优先使用 tiktoken，缺失时回退到字符数/4 的估算。
- **角色差异化保留比例**：
  - Evaluator：保留 50% 历史（评估需要更多上下文）
  - Builder：保留 20% 历史（构建更关注最近操作）
  - 其他：保留 30%
- **Checkpoint 恢复时注入 Git 上下文**：从 `git diff --stat HEAD~5` 提取最近修改，帮助恢复后的 Agent 了解代码变化。

---

## 第5层：配置与提示系统

### 5.1 配置中心 (`config.py`)

**工程控制：**
- **优先级**：环境变量 > `.env` 文件 > 硬编码默认值。
- **API Key 分离**：`API_KEY`（LLM/搜索/视觉）与 `MINIMAX_API_KEY`（图像生成）分离，后者 fallback 到前者。
- **所有阈值外置化**：`MAX_ROUNDS`、`PASS_THRESHOLD`、`DIMENSION_THRESHOLDS`、`COMPRESS_THRESHOLD` 等均可通过环境变量覆盖。

### 5.2 Prompt 系统 (`prompts.py` + `prompts/*.md`)

**工程控制：**
- **懒加载 + 缓存**：`_CACHE` 字典避免重复读取磁盘。
- **模块化**：每个 Agent 对应独立的 `.md` 文件（`builder.md`、`browser_tester.md`、`evaluator.md` 等），便于独立迭代。
- **Skill 注入**：`agents.py` 在 `Agent.__init__` 时调用 `skills.build_catalog_prompt()`，将可用 Skill 列表追加到 system prompt。

### 5.3 Skill 系统 (`skills.py` + `skills/*/SKILL.md`)

**工程控制：**
- **渐进式披露**：Agent 只知道 Skill 的目录（名称+描述），需要时通过 `read_skill_file` 工具显式加载，避免 system prompt 过长。
- **Frontmatter 解析**：从 `---` 分隔的 YAML frontmatter 提取 `description`。

---

## 第6层：支撑基础设施

### 6.1 Dashboard (`dashboard.py`)

**工程控制：**
- **状态机**：`phase ∈ {idle, planning, building, evaluating, done, failed}`，`agent_status ∈ {running, success, error, timeout}`。
- **事件驱动刷新**：每次 `start_agent`/`end_agent`/`update_scores`/`add_alert` 调用 `_flush()`，将状态以 ASCII 表格形式输出到日志。
- **状态并入 Harness 状态**：Dashboard 状态序列化后存入 `harness_state.json` 的 `dashboard` 字段，恢复时一并恢复。

### 6.2 日志系统 (`harness/logging.py`)

**工程控制：**
- ** per-workspace 文件日志**：每个 workspace 有独立的 `logs/harness-{timestamp}.log`。
- **Logger 隔离**：`Harness.__init__` 中为每个实例创建独立 logger (`logging.getLogger(f"harness.{id(self)}")`)，`propagate=False` 防止重复输出。
- **harness.log 指针**：workspace 根目录的 `harness.log` 是一个文本文件（内容为最新日志文件路径），不是 symlink（跨平台兼容）。
- **统计表格**：`log_final_stats()` 输出每轮的 sprint/overall/score/strategy/tokens/time 表格。

### 6.3 Git 管理 (`harness/git.py`)

**工程控制：**
- **自动初始化**：`git init` + 配置 `user.email`/`user.name`。
- **按轮次查找 commit**：`get_commit_for_round()` 通过 `git log --grep "round N snapshot"` 动态解析，不依赖外部状态。
- **原子操作**：`commit_round()` 先检查 `git status --porcelain`，无改动则不 commit。

---

## 关键数据流图

```
┌─────────────┐
│   run.py    │  ← 入口：workspace 创建 + UTF-8 强制
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Harness (harness/core.py)                                  │
│  ├── StateManager ──→ harness_state.json (恢复/持久化)      │
│  ├── GitManager ────→ git init / commit / rollback          │
│  ├── Dashboard ─────→ 实时状态 + alert                      │
│  └── EvalCache ─────→ .eval_cache/ (报告存档+摘要)          │
│                                                             │
│  每轮：plan_sprint → negotiate_contract → Builder.run       │
│         → verify_dev_server → git commit                    │
│         → [Build Gate] → 失败则跳过评估                     │
│         → CodeReviewer ∥ BrowserTester (ThreadPool, 5min)   │
│         → Evaluator.run → parse_scores → dimension check    │
│         → score ≥ 7.0? 结束 : 继续                          │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Agent (agents.py)                                          │
│  ├── ThreadPoolExecutor (4 workers) ──→ run_async()         │
│  ├── OpenAI client ──→ chat.completions.create(tools=...)   │
│  ├── _check_context_lifecycle()                             │
│  │     ├── >48K tokens → inject WorkspaceState 摘要         │
│  │     ├── >80K tokens → compact_messages() (LLM 摘要)      │
│  │     └── >150K tokens → checkpoint + reset                │
│  ├── WorkspaceState.update_from_tool_result() ──→ save()    │
│  └── AgentRunLog ──→ JSONL 事件 (.events/*.jsonl)           │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Tools (tools_impl.py)                                      │
│  ├── 文件操作：_resolve() 路径沙箱 + 30KB 截断               │
│  ├── write_file：debounced auto-build (30s cooldown)        │
│  ├── edit_file：精确匹配 + 多重匹配拒绝 + partial hint      │
│  ├── list_files：mtime 缓存 + 10K 截断 + 默认排除            │
│  ├── run_bash：超时(900s) + 后台检测 + 智能截断(head+error+tail)│
│  ├── start_dev_server：端口清理 + .next 清理 + build 预检   │
│  ├── browser_test/evaluate：per-call asyncio.run()          │
│  │                          _wrap_script() 序列化包装        │
│  ├── generate_image/search_web/analyze_image：MiniMax API   │
│  └── validate_build：启发式错误检测 + WorkspaceState 同步    │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  State & Context                                            │
│  ├── WorkspaceState (.workspace_state.json)                 │
│  │     ├── files: dict[path → FileState]                    │
│  │     ├── last_build_status / last_test_status             │
│  │     ├── dev_server_running / port                        │
│  │     └── current_sprint_goal / completed_tasks            │
│  ├── Context (context.py)                                   │
│  │     ├── count_tokens() (tiktoken / 字符估算)              │
│  │     ├── detect_anxiety() (正则信号检测)                   │
│  │     ├── compact_messages() (角色差异化保留比例)           │
│  │     └── create_checkpoint() → progress.md                │
│  └── EvalCache (.eval_cache/)                               │
│        ├── round_N_code_review.md                           │
│        ├── round_N_browser.md                               │
│        └── round_N_summary.json → EvalSummary.to_markdown() │
└─────────────────────────────────────────────────────────────┘
```

---

## 工程控制总览表

| 控制域 | 机制 | 位置 |
|--------|------|------|
| **并发** | ThreadPoolExecutor (4 workers Agent, 2 workers Eval) | `agents.py`, `harness/core.py` |
| **超时** | Agent 3600s, Eval 300s, Bash 900s, Browser 120s | `agents.py`, `harness/core.py`, `config.py` |
| **状态持久化** | harness_state.json 原子写入 (.tmp + replace) | `harness/state.py` |
| **恢复** | 从 harness_state.json 恢复 round/score/token/strategy | `harness/core.py _load_state` |
| **Git 快照** | 每轮强制 commit，消息 grep 可定位 | `harness/git.py` |
| **Git 回滚** | Sprint 失败回滚 HEAD，Overall 下降回滚最佳轮次 | `harness/core.py _build_round` |
| **上下文压缩** | 三级策略：State Injection → Compact → Checkpoint | `agents.py _check_context_lifecycle` |
| **Token 预算** | 每轮累加，输出到日志和 Dashboard | `agents.py AgentRunLog` |
| **Build Gate** | WorkspaceState build error → 跳过评估给惩罚分 | `harness/core.py _build_round` |
| **Dev Server 验证** | HTTP 轮询 30s，失败记 alert 但不阻塞 | `harness/build.py verify_dev_server` |
| **维度硬门槛** | 单维度低于阈值则强制 cap 总分 | `harness/eval.py check_dimension_thresholds` |
| **路径安全** | _resolve() 检查路径是否逃逸 workspace | `tools_impl.py _resolve` |
| **错误统一** | 所有工具异常返回 `"[error] ..."` | `tools_impl.py execute_tool` |
| **Bash 截断** | 10K 预算，stderr 优先，中间提取关键错误行 | `tools_impl.py _smart_truncate` |
| **文件截断** | read_file 30KB，list_files 10K 条目 | `tools_impl.py` |
| **缓存** | list_files mtime 缓存，prompt 懒加载缓存 | `tools_impl.py _FILE_LIST_CACHE`, `prompts.py _CACHE` |
| **端口管理** | 启动前 kill 占用进程，.next 缓存清理 | `tools_impl.py _kill_port`, `start_dev_server` |
| **Playwright 隔离** | Per-call asyncio.run()，每调用新建 Bridge | `tools/playwright_mcp.py` |
| **脚本序列化** | _wrap_script() 自动包装裸 JS / await | `tools/playwright_mcp.py` |
| **日志隔离** | per-Harness logger，propagate=False | `harness/core.py __init__`, `harness/logging.py` |
| **事件追踪** | .events/*.jsonl 结构化事件 | `agents.py AgentRunLog.write_jsonl` |
| **策略注入** | Builder 输出 STRATEGY: REFINE/PIVOT，Harness 解析并注入下轮 | `harness/strategy.py`, `harness/build.py` |
| **Skill 渐进披露** | 只注入目录，显式 read_skill_file 加载 | `skills.py`, `agents.py __init__` |
