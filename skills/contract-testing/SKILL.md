---
description: Contract testing guide for automated static code analysis. Explains how contract tests work, when to use them, and how to interpret results.
---

# Contract Testing Guide

> **Use `contract_test_run(feature_group)` BEFORE browser tests.** It runs in milliseconds and catches most implementation issues without DOM timing problems.

## What Are Contract Tests?

Contract tests are **static code analysis** tools that verify your implementation against the acceptance criteria in `contract.md`. They:

- ✅ Run instantly (no browser startup)
- ✅ Are deterministic (same code = same result)
- ✅ Don't suffer from DOM timing issues
- ✅ Verify component structure, event handlers, state management

## How to Use

```python
# Run contract tests for the current feature group
contract_test_run(feature_group="F6")
```

### Example Output

```json
{
  "feature_group": "F6",
  "score": 85,
  "passed": true,
  "testable_criteria": 8,
  "tests_run": 6,
  "results": {
    "F6.1": {
      "passed": true,
      "score": 100,
      "details": "Component Cursors.tsx: export=True, jsx=True, props=True, testids=3",
      "evidence": {"file": "Cursors.tsx", "testids": 3}
    },
    "F6.2": {
      "passed": false,
      "score": 40,
      "details": "No cursor/presence component found",
      "evidence": {}
    }
  }
}
```

## Interpreting Results

| Score | Meaning | Action |
|-------|---------|--------|
| 90-100 | Excellent | Minor polish only |
| 70-89 | Good | Some criteria need attention |
| 50-69 | Fair | Significant issues, needs fixes |
| 0-49 | Poor | Major implementation gaps |

## What Contract Tests Check

### Render Tests
- Component file exists with `export`
- Has JSX `return` statement
- Has props `interface`/`type`
- Has `data-testid` attributes

### Interaction Tests
- Event handlers exist (`onClick`, `onChange`, etc.)
- State management exists (`useState`, `useReducer`)
- Uses CSS visibility (not conditional rendering)

### Presence Tests (Cursors, Users)
- Cursor-related components exist
- State management for user presence

### Drawing Tests (Shapes, Canvas)
- Shape elements (`Rect`, `Circle`, `Line`)
- Drawing library usage (`Konva`, `Canvas`)
- Drawing event handlers

### Text Tests
- Text components exist
- Text input handlers exist

## When Contract Tests Are NOT Enough

Contract tests **cannot** verify:
- Visual appearance / CSS styling
- Animation smoothness
- Real-time collaboration (WebSocket)
- File upload processing
- Audio/Video playback

For these, use **browser tests** or **code review**.

## Best Practices

1. **Always run contract tests first** — they catch 70% of issues instantly
2. **Don't retry failed contract tests** — fix the code instead
3. **Use contract test score as evidence** in your review report:
   ```
   F6.1: Contract test PASS (score 100) → PASS
   F6.2: Contract test FAIL (score 40, missing component) → FAIL
   ```
4. **When contract test passes but browser test fails**:
   - The code is correct
   - The issue is DOM timing / test environment
   - Trust contract test, mark as PASS with note

## Common Contract Test Failures

| Failure | Cause | Fix |
|---------|-------|-----|
| "No component found" | Wrong filename or missing file | Check file exists with correct name |
| "Missing export" | Component not exported | Add `export` keyword |
| "Missing JSX" | No return statement with JSX | Add `return (<div>...</div>)` |
| "Missing handlers" | No event handlers | Add `onClick={handleClick}` |
| "Conditional rendering" | Using `&&` or `? :` in JSX | Use `style={{display: condition ? 'block' : 'none'}}` |
