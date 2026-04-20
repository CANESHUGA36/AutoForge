# Builder Dev Server 验证改进方案

## 问题总结

Builder 在提交代码前没有正确验证 dev server 是否真正能渲染页面。日志中 Builder 的验证过程：

1. `npm run build` 成功（或超时）
2. 启动 dev server 后，curl 返回 `000`（连接失败）或空内容
3. Builder 仍然认为"All verification criteria are passing"并提交代码
4. BrowserTester 发现页面返回 404/500，"missing required error components"

根本原因：Builder 的 Prompt 和 component-testing skill 只要求"Start dev server and manually check the page"，但没有给出**具体的验证步骤和成功标准**。

---

## 改进方案（三层防护）

### 第一层：改进 Builder Prompt（prompts.py）

在 `BUILDER_SYSTEM` 的 `## Build Verification (CRITICAL)` 部分后，新增 **Dev Server 运行时验证** 要求：

```markdown
## Dev Server Runtime Verification (CRITICAL — must pass before commit)

After `npm run build` succeeds, you MUST verify the dev server actually serves the page correctly:

### Step 1: Start the server
```bash
npm run dev
```
Wait at least 15 seconds for first compilation (Next.js initial build is slow).

### Step 2: HTTP health check
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```
Expected: `200`. If you get `000`, `404`, or `500`, the server is NOT ready.

### Step 3: Content verification
```bash
curl -s http://localhost:3000 | grep -o "<title>.*</title>"
```
Expected: Your page title appears. If you get "404", "missing required error components", or empty output, DO NOT commit.

### Step 4: Kill server after verification
```bash
# Stop the dev server before committing
pkill -f "next dev"  # Linux/Mac
taskkill /F /IM node.exe  # Windows
```

### ❌ NEVER commit if:
- curl returns HTTP `000`, `404`, or `500`
- Page title is missing or shows "404" / "error"
- Response contains "missing required error components"
- You did not actually run the curl check

### ✅ Only commit when:
- `npm run build` succeeds with no errors
- `curl` returns HTTP `200`
- Page content contains expected title/elements
```

---

### 第二层：改进 component-testing Skill

修改 `skills/component-testing/SKILL.md` 的 `## Build Verification` 和 `## Final Steps`：

```markdown
## Build Verification

```bash
# 1. Type check
npx tsc --noEmit

# 2. Production build
npm run build

# 3. Dev server runtime verification (CRITICAL)
# Start dev server in background
npm run dev &
sleep 15  # Wait for first compilation

# HTTP health check — MUST return 200
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000)
echo "HTTP Status: $HTTP_CODE"

# Content check — MUST contain expected title
TITLE=$(curl -s http://localhost:3000 | grep -o "<title>.*</title>")
echo "Page title: $TITLE"

# Stop dev server
pkill -f "next dev"
```

**If HTTP code is not 200, or title is missing/contains "404"/"error":**
1. Check `cat .next/trace 2>/dev/null | tail -20` for build errors
2. Read `build-troubleshooting` skill
3. Fix the issue — DO NOT commit

**Common causes of 404/500 after successful build:**
- Google Fonts download failure (socket hang up) → Remove problematic fonts
- Corrupted `.next` cache → `rm -rf .next` and rebuild
- Missing error components → Clear cache and rebuild
```

---

### 第三层：Harness 层自动拦截（harness.py）

在 `_build_round` 方法中，Builder 完成后、提交代码前，增加 **Harness 层强制验证**：

```python
def _verify_dev_server(self, port: int = 3000, max_wait: int = 30) -> tuple[bool, str]:
    """
    验证 dev server 是否真正能渲染页面。
    在 Builder 提交后、Evaluate 前执行。
    
    Returns:
        (success, message)
    """
    import time
    import subprocess
    
    ws = str(self.workspace)
    
    # 1. 确保没有残留进程
    subprocess.run("pkill -f 'next dev'", shell=True, cwd=ws, capture_output=True)
    time.sleep(2)
    
    # 2. 启动 dev server（后台）
    proc = subprocess.Popen(
        "npm run dev",
        shell=True,
        cwd=ws,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    
    # 3. 轮询等待 HTTP 200
    start_time = time.time()
    last_status = ""
    
    while time.time() - start_time < max_wait:
        time.sleep(3)
        try:
            result = subprocess.run(
                f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}",
                shell=True,
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=10,
            )
            status = result.stdout.strip()
            last_status = status
            
            if status == "200":
                # 进一步验证内容
                content_result = subprocess.run(
                    f"curl -s http://localhost:{port} | head -100",
                    shell=True,
                    cwd=ws,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                content = content_result.stdout
                
                # 检查是否是错误页面
                if "404" in content and "This page could not be found" in content:
                    proc.kill()
                    return False, f"Dev server returns 404 page (not real content)"
                
                if "missing required error components" in content:
                    proc.kill()
                    return False, f"Dev server has error component issue (corrupted build)"
                
                if "<title>" in content:
                    proc.kill()
                    return True, f"Dev server OK: HTTP 200 with valid page content"
                    
        except Exception as e:
            last_status = str(e)
    
    proc.kill()
    return False, f"Dev server verification failed: last HTTP status = {last_status}"
```

在 `_build_round` 中插入调用（在 Builder 完成后、commit 前）：

```python
# Build 完成后，Harness 层强制验证 dev server
log.info("Build phase complete — verifying dev server before commit...")
server_ok, server_msg = self._verify_dev_server()
if not server_ok:
    log.error(f"[BUILD_GATE] Dev server verification failed: {server_msg}")
    self.dashboard.add_alert(f"Round {round_num}: Dev server failed — {server_msg}")
    # 不提交代码，返回低分强制修复
    score = config.SPRINT_PASS_THRESHOLD - 1.0
    self.sprint_score_history.append(score)
    self.overall_score_history.append(score)
    self.score_history.append(score)
    # ... 记录统计并返回
    return score

log.info(f"[BUILD_GATE] Dev server verified: {server_msg}")

# 然后再执行 commit
head_hash = self._commit_round(round_num)
```

---

### 第四层：改进 tools.py 的 start_dev_server

修改 `start_dev_server` 函数，增加**内容验证**而不仅是 HTTP 状态码：

```python
# 在健康检查部分（第723行附近）
for attempt in range(3):
    # 不仅检查 HTTP 状态码，还检查内容
    health = run_bash(
        f"curl -s http://localhost:{port} | head -50",
        timeout=10,
    )
    
    # 检查是否是真正的页面内容，而不是错误页
    has_error_page = (
        "404" in health and "This page could not be found" in health
    ) or "missing required error components" in health
    
    if has_error_page:
        return (
            f"[error] Server started but returns error page. "
            f"Content preview: {health[:300]}"
        )
    
    # 检查 HTTP 状态码
    status_code = run_bash(
        f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}",
        timeout=10,
    )
    
    if status_code.strip() == "200" and "<title>" in health:
        return f"Server running on port {port} (pid {_dev_server_proc.pid})"
    
    time.sleep(3)
```

---

## 修复优先级

| 层级 | 改动位置 | 影响 | 实施难度 |
|------|---------|------|---------|
| 1 | `prompts.py` BUILDER_SYSTEM | 让 Builder 知道如何正确验证 | ⭐ 最简单 |
| 2 | `skills/component-testing/SKILL.md` | 给 Builder 具体的检查清单 | ⭐ 简单 |
| 3 | `harness.py` 新增 `_verify_dev_server` | Harness 强制拦截，最可靠 | ⭐⭐ 中等 |
| 4 | `tools.py` `start_dev_server` | 改进工具本身的内容验证 | ⭐⭐ 中等 |

**建议实施顺序：1 → 2 → 4 → 3**

先通过 Prompt 和 Skill 让 Builder 学会正确验证，再增强工具层，最后加 Harness 层兜底。

---

## 预期效果

实施后，Builder 在类似场景下的行为：

1. 构建完成后启动 dev server
2. curl 检查发现返回 404 / "missing required error components"
3. Builder 识别出问题，不提交代码
4. Builder 尝试修复（如移除问题字体、清理缓存重建）
5. 再次验证直到 HTTP 200 且内容正确
6. 才执行 git commit

BrowserTester 再也不会收到损坏的构建产物。
