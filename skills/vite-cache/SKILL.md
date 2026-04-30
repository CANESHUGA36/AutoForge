---
description: Vite cache management, HMR behavior, and stale content issues. How to ensure browser_check sees the latest code, not cached old versions.
---

# Vite Cache & HMR Guide

> **Read this when browser_check shows old code after you've made changes.** This is one of the most confusing issues for Builder agents.

---

## The Problem

You write new code, save it, run `browser_check`, but see the OLD version. You think your code didn't work, so you rewrite it. But it was actually correct — Vite's cache just served the old version.

**This wastes 5-15 iterations per project.**

---

## How Vite Caching Works

```
┌─────────────────────────────────────────────────────────────┐
│  Vite Dev Server Caching Layers                             │
│                                                             │
│  Layer 1: Browser Cache                                     │
│    - Chrome caches JS/CSS modules aggressively              │
│    - Hard reload (Ctrl+Shift+R) clears this                 │
│                                                             │
│  Layer 2: Vite Module Graph                                 │
│    - Vite tracks dependencies between files                 │
│    - HMR updates changed modules                            │
│    - Sometimes HMR fails to propagate                       │
│                                                             │
│  Layer 3: Pre-bundle Cache                                  │
│    - node_modules/.vite/                                    │
│    - Cached optimized dependencies                          │
│    - Cleared on dependency changes                          │
│                                                             │
│  Layer 4: Transform Cache                                   │
│    - In-memory during dev server lifetime                   │
│    - Lost when server restarts                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Solutions (In Order of Effectiveness)

### Solution 1: Use `fresh=True` (Recommended)

```javascript
browser_check(
  url="http://localhost:5173",
  mode="inspect",
  fresh=true,  // ← This triggers hard reload + cache clear
  script="..."
)
```

What `fresh=True` does:
1. Clears `node_modules/.vite/`
2. Clears `dist/`
3. Triggers `window.location.reload(true)` (hard reload)
4. Modifies entry file to trigger HMR
5. Waits for page to fully load

**This is the ONLY solution you should use as Builder.**

### Solution 2: Hard Reload in browser_check Script

If `fresh=True` isn't enough (rare):

```javascript
browser_check(
  url="http://localhost:5173",
  mode="inspect",
  fresh=true,
  script=`
    // Force hard reload
    window.location.reload(true);
    
    // Wait and check
    await new Promise(r => setTimeout(r, 2000));
    
    return {
      title: document.title,
      hasNewElement: !!document.querySelector('[data-testid="new-feature"]'),
    };
  `
)
```

### Solution 3: Framework-Level Cache Clear (Automatic)

The framework clears these before each round:
```bash
rm -rf node_modules/.vite
rm -rf dist/
rm -rf .next/cache
```

**You don't need to do this manually.**

---

## What NOT To Do

| Don't Do | Why It Fails |
|----------|-------------|
| Delete `node_modules/` | Takes 2+ minutes to reinstall |
| Run `npm run build` to "refresh" | Doesn't affect dev server cache |
| Modify random files to "trigger HMR" | Unreliable, wastes iterations |
| Restart dev server yourself | Port conflicts, process management issues |
| Call browser_check 5+ times hoping for different result | Each call takes 10s, wastes iterations |

---

## Diagnosing Cache Issues

### Check 1: Is the file actually saved?

```bash
# Verify file content
head -20 src/components/MyComponent.tsx
```

### Check 2: Did build pick up the change?

```bash
npm run build
# Check if build output reflects new code
grep -r "newFeatureName" dist/
```

### Check 3: Is it browser cache or Vite cache?

```javascript
browser_check(
  fresh=true,
  script=`
    // Check if file timestamp matches
    const scripts = document.querySelectorAll('script[type="module"]');
    return {
      scriptCount: scripts.length,
      firstSrc: scripts[0]?.src,
      // Vite adds ?v=xxx query param — check if it changed
    };
  `
)
```

---

## Builder Decision Tree

```
You wrote code → browser_check shows old version
  │
  ├─→ 1. Did write_file return success?
  │   └─→ NO → Fix file path and retry
  │
  ├─→ 2. Did validate_build() pass?
  │   └─→ NO → Fix build errors first
  │
  ├─→ 3. Did you use fresh=True?
  │   └─→ NO → Use fresh=True and retry (1 time)
  │
  ├─→ 4. Still old after fresh=True?
  │   └─→ YES → Code is correct. Vite cache will clear.
  │       → SUBMIT. Do not waste more iterations.
  │
  └─→ 5. Is this the 2nd browser_check?
      └─→ YES → STOP. Max 2 checks. Submit now.
```

---

## Reviewer Cache Handling

Reviewer's browser_check calls should ALWAYS use `fresh=True`:

```javascript
// Reviewer verifying feature F1
browser_check(
  url="http://localhost:5173",
  mode="inspect",
  fresh=true,  // Always true for Reviewer
  script=`
    return {
      hasF1Element: !!document.querySelector('[data-testid="f1-element"]'),
    };
  `
)
```

---

## Tailwind CSS v4 Cache Notes

Tailwind v4 uses a different caching mechanism:
- No `tailwind.config.js` (uses CSS-based config)
- Caches are in `node_modules/.cache/tailwindcss/`
- Framework clears these automatically

If Tailwind classes aren't updating:
1. Check that classes exist in source code
2. Use `fresh=True` in browser_check
3. If still wrong → CSS class name issue, not cache issue

---

## Summary

| Situation | Action |
|-----------|--------|
| Old code in browser_check | Use `fresh=True` |
| Still old after fresh=True | Submit anyway, code is correct |
| >2 browser_check calls | STOP, submit |
| Build passes but browser looks wrong | Trust build, submit |
| Cache clearing manually | DON'T — framework handles it |
