---
description: React DevTools protocol guide for inspecting React Fiber tree. Bypasses DOM timing issues by checking component existence and props directly.
---

# React DevTools Inspection Guide

> **Use `react_devtools_inspect()` when `browser_check` cannot find dynamically rendered components.** This happens with animations, cursors, real-time updates, and `useEffect`-driven renders.

## The Problem: DOM Timing

React components rendered by `useEffect` + `requestAnimationFrame` may not be in the DOM when `browser_check` runs:

```tsx
// This component renders AFTER mount via useEffect
useEffect(() => {
  const animate = () => {
    setCursors(newCursors); // Triggers re-render
    requestAnimationFrame(animate);
  };
  requestAnimationFrame(animate);
}, []);
```

**Result:** `browser_check` sees empty DOM → reports FAIL ❌  
**Reality:** Component is correct, just not rendered yet

## The Solution: React DevTools Protocol

React DevTools inspects the **Fiber tree** — React's internal representation — which exists even before DOM commit:

```python
# Check if CursorElement exists in React tree (not DOM)
react_devtools_inspect(component_name="CursorElement")
```

## How to Use

### Basic Component Check

```python
react_devtools_inspect(component_name="Cursors")
# Returns: {"component": "Cursors", "found": true, "count": 1}
```

### Check with Props

```python
react_devtools_inspect(
    component_name="CursorElement",
    check_props={"visible": True}
)
# Returns: {"component": "CursorElement", "found": true, "count": 3, "props_match": true}
```

### When Browser Test Fails

```python
# 1. Browser test fails (DOM timing)
browser_check(
    url="http://localhost:5173",
    mode="inspect",
    script="return !!document.querySelector('.cursor')"
)
# → Returns: false (cursors not in DOM yet)

# 2. React DevTools confirms component exists
react_devtools_inspect(component_name="CursorElement")
# → Returns: {"found": true, "count": 3}

# 3. Conclusion: Code is correct, DOM timing issue → PASS
```

## When to Use React DevTools

| Scenario | Use DevTools? | Why |
|----------|--------------|-----|
| Cursors / user presence | ✅ Yes | Rendered by animation loop |
| Real-time updates | ✅ Yes | WebSocket-driven renders |
| `useEffect` + `setInterval` | ✅ Yes | Delayed rendering |
| Static components | ❌ No | browser_check works fine |
| Form inputs | ❌ No | Code review is sufficient |
| Buttons / static UI | ❌ No | browser_check is reliable |

## Interpreting Results

```json
{
  "component": "CursorElement",
  "found": true,
  "count": 3,
  "props_match": true,
  "actual_props": {
    "cursor": "[object]",
    "viewport": "[object]",
    "visible": true
  }
}
```

| Field | Meaning |
|-------|---------|
| `found` | Component exists in React tree |
| `count` | Number of instances |
| `props_match` | Props match expected values |
| `actual_props` | Actual props (functions shown as `[function]`) |

### When `found: false`

```json
{
  "component": "CursorElement",
  "found": false,
  "similar_components": ["Cursors", "CursorOverlay"],
  "all_components": ["App", "Cursors", "Stage", "Layer", "Text"]
}
```

**Action:**
1. Check `similar_components` — maybe the name is slightly different
2. Check `all_components` — see what's actually in the tree
3. If component should exist but doesn't → **FAIL** (code issue)

## DevTools vs Browser Check Decision Tree

```
Need to verify component exists?
│
├─→ Is it rendered by useEffect / animation / WebSocket?
│   ├─→ YES → react_devtools_inspect (Fiber tree is always there)
│   └─→ NO  → browser_check (DOM is sufficient)
│
├─→ browser_check returns false?
│   ├─→ Is it dynamic content? → react_devtools_inspect to confirm
│   └─→ Is it static content?  → FAIL (should be in DOM)
│
└─→ react_devtools_inspect returns false?
    └─→ FAIL (component not in React tree = not implemented)
```

## Important Notes

1. **DevTools requires a browser session** — run `browser_check` first to initialize the page
2. **Hook installation is automatic** — no manual setup needed
3. **Fiber tree extraction may fail** if React is not loaded — check `browser_check` works first
4. **Props are simplified** — complex objects show as `[object]`, functions as `[function]`

## Common Patterns

### Verify Cursors Component
```python
# Check container exists
react_devtools_inspect(component_name="Cursors")

# Check individual cursors
react_devtools_inspect(component_name="CursorElement")
```

### Verify Shape Tools
```python
# Check Konva shapes
react_devtools_inspect(component_name="Rect")
react_devtools_inspect(component_name="Circle")
react_devtools_inspect(component_name="Line")
```

### Verify Text Component
```python
react_devtools_inspect(component_name="Text")
```

## Limitations

- Cannot check CSS styles
- Cannot verify visual appearance
- Cannot test user interactions
- Requires active browser session
- Props inspection is shallow

**For these, use browser_check or code review.**
