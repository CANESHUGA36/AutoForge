# AutoForge 最新代码工程控制层面 Bug 分析报告

> 基于 commit `c5b7b5f`（main 分支最新）的静态分析。对比上一版 `7eff02f`。

---

## 变更概览

用户已按之前的重构方案完成了大量改动：

| 改动项 | 状态 |
|--------|------|
| 9 → 5 Agent 简化 | ✅ 已完成 |
| 工具集按职能细分 | ✅ 已完成（`allowed_tools`） |
| Playwright MCP 连接池 | ✅ 已完成（`PlaywrightMCPPool`） |
| 动态迭代预算 | ✅ 已实现（`_inject_iteration_budget`） |
| 动态轮数限制 | ✅ 已实现（`_calculate_max_rounds`） |
| 趋势摘要 | ✅ 已实现（`_build_trend_summary`） |
| 框架 pytest 测试 | ✅ 已添加（`tests/` 目录） |
| SprintMaster 合并 | ✅ 已完成（`plan_sprint_master`） |

但仍有 **老 Bug 未修复** 且 **新 Bug 被引入**。

---

## 第一部分：仍然存在的旧 Bug（6 个）

### 1. 🔴 Build Gate 仍在 Git commit 之后

**位置：** `harness/core.py:_build_round()` ~line 200

**现状：**

```python
self.git.commit_round(round_num)          # ← commit 先执行
# ... 然后 Build Gate 检查 ...
if ws_data.get("last_build_status") == "error":
    # 跳过评估，但 commit 已经做了！
    return config.SPRINT_PASS_THRESHOLD - 0.5
```

**问题：**
- `commit_round(round_num)` 在 Build Gate 之前无条件执行
- 如果 build error，坏代码已被提交到 Git
- 后续回滚可能回滚到这个坏 commit（`get_commit_for_round(round_num)` 查找时）

**修复建议（同之前）：**

方案 A：Build Gate 移到 commit 之前
```python
# Build Gate 在 commit 之前
if self._has_build_error():
    return config.SPRINT_PASS_THRESHOLD - 0.5

# 只有 build 通过了才 commit
self.git.commit_round(round_num)
```

方案 B：Build Gate 触发时 rollback 到上一轮
```python
if ws_data.get("last_build_status") == "error":
    prev_hash = self.git.get_commit_for_round(round_num - 1)
    if prev_hash:
        self.git.rollback_to(prev_hash, "Build Gate: build error")
    return config.SPRINT_PASS_THRESHOLD - 0.5
```

---

### 2. 🔴 维度硬门槛后 score 不一致

**位置：** `harness/core.py:_build_round()` ~line 270

**现状：**

```python
score = overall_score                        # ← 先赋值
failed_dims = check_dimension_thresholds(dim_scores)
if failed_dims:
    if score >= config.PASS_THRESHOLD:
        score = config.PASS_THRESHOLD - 0.1  # ← 只改了局部变量 score
```

**问题：**
- `score` 被 cap 了，但 `overall_score` 未被修改
- `self.overall_score_history.append(overall_score)` 记录的仍是原始高分
- 后续判断 `overall_score_history[-1] < best_score - SIGNIFICANT_DROP` 时，用的是未 cap 的值，可能漏过回滚条件

**修复：**

```python
if failed_dims:
    self.log.warning(f"Hard threshold(s) failed: {', '.join(failed_dims)}")
    if score >= config.PASS_THRESHOLD:
        score = config.PASS_THRESHOLD - 0.1
        overall_score = config.PASS_THRESHOLD - 0.1  # ← 同时 cap
        self.log.warning(f"Scores capped to {score}")
```

---

### 3. 🔴 `.workspace_state.json` 的 `files` 字段加载丢失

**位置：** `workspace_state.py:load()` ~line 250

**现状：**

```python
@classmethod
def load(cls, workspace: str) -> "WorkspaceState":
    # ...
    state.total_files = data.get("total_files", 0)
    state.total_lines = data.get("total_lines", 0)
    state.last_build_status = data.get("last_build_status", "unknown")
    # files 字段呢？完全没有恢复！
    return state
```

**问题：**
- `to_dict()` 保存了完整的 `files` 字典
- `load()` 完全忽略，每次重启后 `state.files` 为空
- `summarize()` 列出的"最近修改的 10 个文件"永远是空的
- State Injection 后 Builder 看不到任何文件列表

**修复（同之前）：**

```python
files_data = data.get("files", {})
for path, fdata in files_data.items():
    state.files[path] = FileState(
        path=fdata.get("path", path),
        size=fdata.get("size", 0),
        lines=fdata.get("lines", 0),
        summary=fdata.get("summary", ""),
    )
state.total_files = len(state.files)
state.total_lines = sum(f.lines for f in state.files.values())
```

---

### 4. 🟠 `validate_build` 启发式检测仍然脆弱

**位置：** `tools_impl.py:validate_build()`

**现状：**

```python
has_real_error = (
    "error" in build_result.lower()
    and "0 errors" not in build_result.lower()
    and "compiled successfully" not in build_result.lower()
    and "build succeeded" not in build_result.lower()
)
```

**问题：**
- 用户已改进了 `run_bash`（现在返回 `[exit code: N]`），但 `validate_build` 仍然用字符串匹配
- `"error handling configured"` → 误判
- `"0 errors found in lint"` → `"0 errors"` 检查通过，但 `"error"` 也匹配 → 误判

**修复：**

`run_bash` 已返回 exit code，直接用：

```python
exit_code_match = re.search(r'\[exit code:\s*(\d+)\]', build_result)
if exit_code_match:
    exit_code = int(exit_code_match.group(1))
    has_real_error = exit_code != 0
else:
    # fallback 到启发式
    has_real_error = ...
```

---

### 5. 🟡 `browser_test` 异常时误杀 dev server

**位置：** `tools_impl.py:browser_test()`

**现状：**

```python
try:
    return browser_test_mcp(...)
except Exception as e:
    _kill_dev_server()  # ← 全局清理所有 server
    return f"[error] Browser test failed: {e}"
```

**问题：**
- `_kill_dev_server()` 会杀掉端口 3000 和 5173 上的所有进程
- 如果 Builder 还在运行 dev server，Reviewer 的测试失败后会把 Builder 的 server 也杀掉
- 虽然串行执行中不太会发生，但这是潜在风险

**修复：**

```python
try:
    return browser_test_mcp(...)
except Exception as e:
    # 只杀掉自己启动的 server
    if start_command:
        _kill_port(port)
    return f"[error] Browser test failed: {e}"
```

---

### 6. 🟡 `_llm_call_simple` 无异常处理

**位置：** `agents.py:_llm_call_simple()`

**现状：**

```python
def _llm_call_simple(self, messages: list[dict]) -> str:
    response = client.chat.completions.create(...)
    return response.choices[0].message.content or ""
```

**问题：**
- 无 try/except，API 超时/限流/网络故障直接崩溃
- 在 token 临界点（`create_checkpoint`、`compact_messages`）时崩溃最致命
- 状态丢失，没有 checkpoint 留存

**修复：**

```python
def _llm_call_simple(self, messages: list[dict]) -> str:
    try:
        response = client.chat.completions.create(...)
        return response.choices[0].message.content or ""
    except Exception as e:
        self._log.error(f"[_llm_call_simple] LLM call failed: {e}")
        return "[Summary generation failed due to API error. Continuing with truncated context.]"
```

---

## 第二部分：新引入的 Bug（7 个）

### 7. 🔴 Playwright MCP `context_id` 不匹配 — 资源泄漏

**位置：** `tools_impl.py:browser_test()` + `harness/core.py:_build_round()` 末尾

**现状：**

```python
# tools_impl.py:browser_test()
def browser_test(...):
    return browser_test_mcp(url=url, actions=actions, ...)  # ← 没传 context_id！
    # 默认 context_id="default"

# harness/core.py 每轮结束
try:
    from tools.playwright_mcp import close_mcp_bridge
    close_mcp_bridge("reviewer")  # ← 释放 "reviewer"
except Exception as e:
    ...
```

**问题：**
- `browser_test_mcp` 默认 `context_id="default"`
- `_build_round` 释放 `"reviewer"`
- 两者不匹配！`"default"` context 的 Bridge 永远不会被释放
- 每轮创建一个新的 Bridge 实例（因为 `"default"` 已经在 `_bridges` 里？不，`_pool.get("default")` 会复用已有的）
- 但如果 Bridge 卡死或出问题，下一次 `get("default")` 仍返回同一个坏实例

**实际上更严重的问题：**

```python
# browser_test_mcp 的实现
async def _run() -> str:
    bridge = await _pool.get(context_id)  # context_id="default"
    return await bridge.browser_test(...)
```

如果 Reviewer 调用 `browser_test` 5 次（桌面 + 移动端 + 不同页面），这 5 次都复用同一个 `"default"` Bridge——这是好的。但问题在：

1. Reviewer 用的是 `"default"`
2. `_build_round` 释放 `"reviewer"`
3.  `"default"` 的 Bridge 在 Harness 整个生命周期中从不被释放
4. 如果进程退出时没有调用 `close_mcp_bridge()`（无参数），Chromium 进程可能残留

**修复：**

方案 A：统一 context_id
```python
# tools_impl.py
def browser_test(..., context_id: str = "reviewer"):
    return browser_test_mcp(..., context_id=context_id)

# harness/core.py 末尾
close_mcp_bridge("reviewer")  # ← 匹配
```

方案 B：每轮结束后释放所有
```python
close_mcp_bridge()  # 不传参数 = 释放全部
```

---

### 8. 🔴 `asyncio.run()` 嵌套事件循环风险

**位置：** `tools/playwright_mcp.py:browser_test_mcp()` / `close_mcp_bridge()`

**现状：**

```python
def browser_test_mcp(...) -> str:
    async def _run() -> str:
        bridge = await _pool.get(context_id)
        return await bridge.browser_test(...)
    return asyncio.run(_run())  # ← 创建新的事件循环
```

**问题：**
- `asyncio.run()` 在已有事件循环中调用会抛出 `RuntimeError`
- 如果未来 Agent 改为异步执行（`run_async`），或在 Jupyter/async 环境中运行，直接崩溃
- `close_mcp_bridge()` 同样的问题

**修复：**

使用 `asyncio.get_event_loop()` + `create_task` 方案，或检测已有 loop：

```python
def browser_test_mcp(...) -> str:
    async def _run() -> str:
        bridge = await _pool.get(context_id)
        return await bridge.browser_test(...)
    
    try:
        loop = asyncio.get_running_loop()
        # 已在事件循环中，需要不同的调度方式
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _run()).result()
    except RuntimeError:
        return asyncio.run(_run())
```

更简单的方案：在 Harness 层启动时初始化一个持久的事件循环线程：

```python
class PlaywrightMCPPool:
    def __init__(self):
        self._bridges = {}
        self._lock = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
    
    def get_sync(self, context_id: str = "default") -> PlaywrightMCPBridge:
        """同步接口，内部委托给事件循环线程"""
        future = asyncio.run_coroutine_threadsafe(self.get(context_id), self._loop)
        return future.result()
```

---

### 9. 🔴 动态轮数只在启动时计算一次

**位置：** `harness/core.py:run()` ~line 120

**现状：**

```python
max_rounds = self._calculate_max_rounds()          # ← 只计算一次
for round_num in range(start_round, max_rounds + 1):
    # ...
    score = self._build_round(round_num)
```

**问题：**
- `_calculate_max_rounds()` 的意图是"运行时根据 Builder 表现动态调整"
- 但 `max_rounds` 在循环开始前固定了
- 策略调整（如"连续 PIVOT 追加 2 轮"）在运行中不生效
- 这意味着 `_runtime_adjustment` 和 `_strategy_adjustment` 只在启动时生效一次

**修复：**

```python
round_num = start_round
while True:
    max_rounds = self._calculate_max_rounds()  # ← 每轮重新计算
    if round_num > max_rounds:
        break
    
    score = self._build_round(round_num)
    # ...
    round_num += 1
```

---

### 10. 🟠 `plan_sprint_master` 无错误处理

**位置：** `harness/sprint.py:plan_sprint_master()`

**现状：**

```python
def plan_sprint_master(...):
    sprint_master.run(task)
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        log.warning("SprintMaster did not create sprint.md")
```

**问题：**
- 如果 SprintMaster 运行失败（API 错误、超时、异常），`run()` 返回 `"[error] ..."`
- 但 `plan_sprint_master` 不检查返回值，只检查文件是否存在
- SprintMaster 失败后，Builder 会读到一个**旧版 sprint.md** 或**不存在的 sprint.md**
- Builder 基于错误/过期的 sprint 工作，浪费迭代

**修复：**

```python
def plan_sprint_master(...):
    result = sprint_master.run(task)
    if result.startswith("[error]"):
        log.error(f"SprintMaster failed: {result}")
        # 选项 1: 使用上一轮 sprint
        # 选项 2: 终止本轮
        # 选项 3: 基于 spec 生成一个最小 sprint
        return False
    
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        log.error("SprintMaster did not create sprint.md")
        return False
    return True
```

---

### 11. 🟠 `_inject_iteration_budget` 正则脆弱

**位置：** `harness/core.py:_inject_iteration_budget()`

**现状：**

```python
def _inject_iteration_budget(self, build_task: str) -> str:
    text = sprint_path.read_text(...)
    match = re.search(r'- 保守：(\d+) 次', text)  # ← 固定中文格式
    conservative = int(match.group(1)) if match else 25
```

**问题：**
- 完全依赖 SprintMaster 输出 `"- 保守：30 次"` 的精确格式
- 如果 SprintMaster 输出 `"- 保守: 30 次"`（半角冒号）或 `"- Conservative: 30"`（英文），匹配失败
- 回退到默认 25，可能和实际预算不符

**修复：**

更宽松的正则：
```python
# 匹配多种格式：保守/Conservative，冒号/半角冒号，次/iterations
patterns = [
    r'(?:保守|Conservative)[：:]\s*(\d+)(?:\s*次| iterations)?',
    r'(?:estimated|budget|limit)[：:]\s*(\d+)(?:\s*iterations)?',
]
for p in patterns:
    match = re.search(p, text, re.I)
    if match:
        conservative = int(match.group(1))
        break
else:
    conservative = 25
```

---

### 12. 🟠 `verify_dev_server` 在 commit 之后但失败不阻塞

**位置：** `harness/core.py:_build_round()` ~line 210

**现状：**

```python
# 1. Builder 运行
build_result, build_usage = self.builder.run_with_stats(build_task)

# 2. 策略解析
strategy = parse_strategy(build_result)

# 3. verify_dev_server（在 commit 之前，但失败不阻塞！）
server_ok, server_msg = verify_dev_server(self.workspace)
if not server_ok:
    log.warning(f"[build_gate] Dev server verification failed: {server_msg}")

# 4. Git commit（无条件执行）
self.git.commit_round(round_num)
```

**问题：**
- `verify_dev_server` 调用 `curl http://localhost:{port}` 检查 server 是否运行
- 如果 server 没运行（如 Builder 没启动或启动失败），只是 warning，不阻塞
- 然后代码仍然被 commit
- 之后 Reviewer 调用 `browser_test`，会尝试 `start_dev_server` 或直接用已有的 server
- 如果 server 确实没运行，Reviewer 的浏览器测试会失败

**这不是严重 bug**，但逻辑上：
- Build Gate 检查的是 `last_build_status`（构建状态）
- `verify_dev_server` 检查的是运行时状态
- 两者之间没有因果关系，而且位置在 commit 之前却不阻塞

**建议：**

将 `verify_dev_server` 改为阻塞性检查，或移到 Reviewer 阶段：

```python
# 如果 dev server 必须运行才能评估，且当前没运行，要么：
# A) 阻塞，让 Builder 修复
# B) 在 Reviewer 阶段自动启动（Reviewer 已经有 start_dev_server 工具）
```

---

### 13. 🟡 `_estimate_from_spec` 启发式计数不准确

**位置：** `harness/core.py:_estimate_from_spec()`

**现状：**

```python
def _estimate_from_spec(self) -> int:
    spec_text = spec_path.read_text()
    feature_lines = [l for l in spec_text.splitlines()
                     if l.strip().startswith("-") and "feature" in l.lower()]
    feature_count = len(feature_lines)
    asset_count = spec_text.count("generate_image")
    # ...
```

**问题：**
- 如果 spec 中写 `"Key Features"` 作为 Markdown 标题，这行会被算作一个功能
- 如果功能列表用 `1.` 编号而非 `-`，统计失败
- `spec_text.count("generate_image")` 会计数代码示例中的 `generate_image(...)` 调用
- 这只是一个估算，但如果估算偏差太大，`max_rounds` 会偏离实际需求

**修复：**

使用更严格的 Markdown 解析：

```python
import re

# 只统计 Features 章节下的列表项
features_section = re.search(
    r'##\s*Features?.*?(?=##|\Z)',
    spec_text,
    re.I | re.S
)
if features_section:
    section_text = features_section.group(0)
    feature_lines = re.findall(r'^[\s]*[-*]\s+.+', section_text, re.M)
    feature_count = len(feature_lines)
else:
    feature_count = 0

# 资产只统计 generate_image 工具的调用，排除代码示例
asset_count = len(re.findall(r'generate_image\s*\(', spec_text))
```

---

## 第三部分：测试中发现的缺口

### 14. 测试覆盖不完整

新增 `tests/` 目录但仍有缺口：

| 测试文件 | 已有测试 | 缺失测试 |
|---------|---------|---------|
| `test_eval.py` | `parse_scores`, `check_dimension_thresholds` | `parse_dimension_scores`（维度解析逻辑） |
| `test_tools_impl.py` | 基础功能 | `_resolve` 路径沙箱（最重要！防逃逸） |
| `test_harness_core.py` | 动态轮数计算 | Build Gate 逻辑、回滚触发条件、评分 cap 逻辑 |
| `test_context.py` | 基础 | `_safe_split_index`（tool_calls 链安全） |
| `test_state.py` | save/load | `files` 字段序列化/反序列化 |

**最应补充：** `_resolve` 的路径沙箱测试（当前完全未覆盖）。

---

## 优先级矩阵

| 优先级 | Bug | 类型 | 修复难度 |
|--------|-----|------|---------|
| P0 | #7 Playwright `context_id` 不匹配 | 新引入 | 小 |
| P0 | #9 动态轮数只算一次 | 新引入 | 小 |
| P0 | #1 Build Gate 在 commit 后 | 旧 | 中 |
| P0 | #3 `files` 字段加载丢失 | 旧 | 小 |
| P1 | #2 维度硬门槛 score 不一致 | 旧 | 小 |
| P1 | #8 `asyncio.run()` 嵌套风险 | 新引入 | 中 |
| P1 | #10 `plan_sprint_master` 无错误处理 | 新引入 | 小 |
| P2 | #4 `validate_build` 启发式 | 旧 | 小 |
| P2 | #11 迭代预算正则脆弱 | 新引入 | 小 |
| P2 | #14 测试覆盖缺口 | 新引入 | 中 |

---

*报告基于 commit `c5b7b5f`（2026-04-24 推送）*
