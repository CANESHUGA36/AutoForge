---
description: Dev server lifecycle management for Builder and Reviewer agents. When to start, when to stop, how to handle port conflicts, cache issues, and stale content.
---

# Dev Server Management

> **For framework developers and agent operators.** Understanding dev server behavior prevents the most common source of "localhost unreachable" and "stale code" issues.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Harness Round                                              │
│                                                             │
│  1. [Framework] Kill existing dev server                   │
│  2. [Framework] Clear caches (.vite, .next, dist)          │
│  3. [Framework] Start fresh dev server                     │
│  4. [Framework] Wait for health check (port ready)         │
│  ─────────────────────────────────────────────────────     │
│  5. [Builder] Write code                                   │
│  6. [Builder] validate_build() → npm run build             │
│  7. [Builder] browser_check (optional, max 2)              │
│  ─────────────────────────────────────────────────────     │
│  8. [Reviewer] browser_check (mode=inspect/interact)       │
│  9. [Reviewer] Code review via read_file                   │
│  10. [Judge] Score based on Reviewer report                │
└─────────────────────────────────────────────────────────────┘
```

**Key insight**: Dev server is managed by the framework, NOT by Builder or Reviewer.

---

## For Builder Agents

### Rule 1: NEVER Start Dev Server Yourself

```bash
# ❌ NEVER DO THIS
npm run dev &
npx vite &
npx next dev &
```

Why:
- Background processes are killed when command timeout expires
- Creates port conflicts (5173, 3000 already in use)
- Wastes 3-5 iterations on server management
- Framework already started the server before you began

### Rule 2: If browser_check Fails with "localhost unreachable"

**This is an environment issue, not your code issue.**

```
browser_check fails → "Can't reach localhost:5173"
  │
  ├─→ Did validate_build() pass?
  │   ├─→ YES → Code is correct. Submit immediately.
  │   └─→ NO  → Fix build errors first.
  │
  └─→ Do NOT try to start dev server yourself
```

### Rule 3: If browser_check Shows Old Code

```
browser_check shows old component code
  │
  ├─→ 1. Confirm write_file returned success
  ├─→ 2. Run validate_build() to confirm build passes
  ├─→ 3. Use browser_check(fresh=True) — forces hard reload + cache clear
  └─→ 4. If STILL old → Code is correct, submit. Vite HMR will catch up.
```

**Do NOT**:
- Delete node_modules
- Restart dev server
- Modify files to "trigger HMR"
- Call browser_check more than 2 times

---

## For Framework Developers

### Startup Sequence (Before Builder)

```python
# harness/core.py — before Builder phase

# 1. Kill existing server
_kill_dev_server()  # Kills ports 3000, 5173

# 2. Clear caches
clear_vite_cache()
clear_next_cache()

# 3. Wait for port release
time.sleep(2)

# 4. Start fresh server
start_dev_server("npm run dev", port=detect_port(), wait=15)

# 5. Verify with health check
# Built into start_dev_server()
```

### Cache Clearing Strategy

| Cache Location | When to Clear | Why |
|---------------|---------------|-----|
| `node_modules/.vite` | Every round start | Vite pre-bundle cache |
| `.next/cache` | Every round start | Next.js build cache |
| `.next/turbopack` | Every round start | Turbopack dev cache |
| `dist/` | Every round start | Production build output |

### Port Detection

```python
def detect_project_port(workspace):
    """Detect which port the project uses."""
    pkg = json.loads((workspace / "package.json").read_text())
    scripts = pkg.get("scripts", {})
    
    dev_script = scripts.get("dev", "")
    if "--port" in dev_script:
        # Extract port from script
        match = re.search(r'--port\s+(\d+)', dev_script)
        return int(match.group(1)) if match else 5173
    
    # Default ports by framework
    if "next" in dev_script:
        return 3000
    return 5173  # Vite default
```

---

## Common Issues & Solutions

### Issue 1: "Port already in use"

**Cause**: Previous dev server didn't fully terminate.

**Fix**:
```bash
# Kill all node processes on the port
lsof -ti:5173 | xargs kill -9 2>/dev/null
fuser -k 5173/tcp 2>/dev/null
```

### Issue 2: "Stale content after file change"

**Cause**: Vite HMR didn't pick up the change, or browser cached the old version.

**Fix in browser_check**:
```python
# tools/playwright_mcp.py
if fresh:
    # Hard reload
    await page.evaluate("() => window.location.reload(true)")
    await asyncio.sleep(wait + 1)
    
    # Trigger HMR by modifying entry file
    entry_file = workspace / "src" / "main.tsx"
    if entry_file.exists():
        original = entry_file.read_text()
        entry_file.write_text(original + "\n")
        time.sleep(0.1)
        entry_file.write_text(original)
```

### Issue 3: "Dev server starts but health check fails"

**Cause**: Server needs more time to compile on first start.

**Fix**: Increase wait time for complex projects:
```python
# Complex projects (many dependencies, large codebase)
wait = max(default_wait, 20)  # Wait up to 20 seconds

# Simple projects
wait = max(default_wait, 10)  # 10 seconds is enough
```

### Issue 4: "Builder killed dev server by running npm run build"

**Cause**: `npm run build` in Vite may interfere with running dev server.

**Fix**: Build and dev server use different output directories:
```javascript
// vite.config.ts
export default defineConfig({
  build: {
    outDir: 'dist',  // Production build
  },
  // Dev server uses in-memory output, doesn't conflict
})
```

---

## Port Reference

| Framework | Default Port | Config Location |
|-----------|-------------|-----------------|
| Vite | 5173 | `vite.config.ts` → `server.port` |
| Next.js | 3000 | `next.config.js` → no direct config, use `-p` flag |
| Pure HTML | N/A | File protocol, no server |

---

## Health Check Implementation

```python
def start_dev_server(command, port, wait=15):
    """Start dev server and verify it's ready."""
    # ... start process ...
    
    # Wait for server to be ready
    time.sleep(wait)
    
    # Health check
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}", 
                method="HEAD"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return f"Server running on port {port}"
        except Exception:
            time.sleep(2)
    
    return f"[error] Server health check failed after {wait}s + 10s retries"
```

---

## Checklist for New Projects

Before first Builder round:
- [ ] Dev server starts successfully on correct port
- [ ] Health check returns 200
- [ ] Cache directories are cleared
- [ ] Builder knows NOT to start dev server
- [ ] browser_check(fresh=True) shows current code
