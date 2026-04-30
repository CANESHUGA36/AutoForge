---
description: Reviewer agent strategies for efficient code review and browser testing. How to avoid disaster loops, verify features correctly, and produce reliable reports.
---

# Reviewer Patterns & Strategies

> **Read this BEFORE starting a review.** It will save you from wasting 20+ iterations on impossible tests and produce better reports.

---

## Core Principle: Code Review > Browser Tests

**Your primary job is code review. Browser tests are supplementary.**

If you can verify functionality by reading source code, **do not waste iterations on browser automation**.

| Verification Method | Priority | When to Use |
|-------------------|----------|-------------|
| Code review (read_file) | **PRIMARY** | Always start here |
| DOM existence check | Secondary | Verify elements are rendered |
| Interaction testing | Last resort | Only for non-React native events |

---

## The Disaster Loop (What NOT To Do)

### The Pattern

1. You see a feature that requires user input (text field, button click)
2. You try `browser_check` with `element.click()`
3. Nothing happens (React synthetic events don't respond)
4. You try `dispatchEvent(new MouseEvent('click'))`
5. Nothing happens
6. You try adding `setTimeout`, busy waits, different selectors
7. 20 iterations later, you've tested nothing meaningful

### Real Example (26 Iterations Wasted)

```
Iteration 1:  browser_check → click input → check value → FAIL
Iteration 2:  browser_check → dispatchEvent → check value → FAIL
Iteration 3:  browser_check → try different selector → FAIL
...
Iteration 26: Still trying to trigger React controlled input
```

**The code was correct all along.** The browser just couldn't trigger React events.

---

## The Correct Strategy

### Step 1: Code Review (Always First)

For each feature criterion, check:

```
F1.1: User can input task text
  ├─→ read_file src/components/TaskInput.tsx
  ├─→ Check: <input> element exists? ✅
  ├─→ Check: onChange handler bound? ✅
  ├─→ Check: onKeyDown handles Enter? ✅
  ├─→ Check: State updates correctly? ✅
  └─→ VERDICT: CODE PASS (no browser test needed)
```

### Step 2: DOM Verification (If Needed)

Only verify that elements exist in DOM:

```javascript
browser_check(
  mode="inspect",
  fresh=true,
  script=`
    return {
      // Verify DOM structure only
      hasInput: !!document.querySelector('[data-testid="f1-task-input"]'),
      hasButton: !!document.querySelector('[data-testid="f1-add-button"]'),
      hasList: !!document.querySelector('[data-testid="f1-task-list"]'),
    };
  `
)
```

### Step 3: Mark Browser Limitations

If browser couldn't test something, explicitly say so:

```markdown
## Code Review Findings
- [x] F1.1: JSX exists, onChange handler non-empty → **CODE PASS**

## Browser Test Limitations (Do Not Affect Score)
- F1.1: React controlled input cannot be triggered via JavaScript → Test skipped, rely on code review
```

---

## What CANNOT Be Browser-Tested

| Feature | Why | Your Action |
|---------|-----|-------------|
| React controlled input | Synthetic event system blocks programmatic events | Code review only |
| React button onClick | `element.click()` doesn't trigger React handlers | Code review only |
| Zustand/Redux state | Internal state not observable from DOM | Code review only |
| Form onSubmit | React intercepts submit | Code review only |
| useEffect side effects | Timing-dependent, not DOM-visible | Code review only |

## What CAN Be Browser-Tested

| Feature | How | Example |
|---------|-----|---------|
| DOM element existence | `querySelector` | `!!document.querySelector('[data-testid="x"]')` |
| CSS visibility | `getComputedStyle` | `getComputedStyle(el).display !== 'none'` |
| Canvas rendering | `canvas.toDataURL()` | Check pixel data |
| Native event handlers | `addEventListener` | Non-React vanilla JS |
| File upload | `DataTransfer` + `dispatchEvent` | See browser-testing skill |

---

## Report Format (Mandatory)

Your report MUST separate code findings from browser limitations:

```markdown
## Code Review Findings

### F1: Task Management
- [x] F1.1: Input component exists with onChange + onKeyDown → **CODE PASS**
- [x] F1.2: Add button exists with onClick → **CODE PASS**
- [ ] F1.3: Task list missing → **CODE FAIL**

### F2: Data Persistence
- [x] F2.1: localStorage usage in useEffect → **CODE PASS**

## Browser Test Results
- F1.1: DOM elements present ✓
- F1.2: Button clickable (native event) ✓

## Browser Test Limitations (Do Not Affect Score)
- F1.1: React controlled input could not be triggered → Rely on code review
- F1.2: React onClick could not be triggered → Rely on code review
```

**Judge will ignore "Browser Test Limitations" section.** Only "Code Review Findings" matter for scoring.

---

## Iteration Budget

| Phase | Max Iterations | Action If Exceeded |
|-------|---------------|-------------------|
| Code review | 10 | You've read all files, move to browser |
| Browser DOM check | 3 | Stop, report what you have |
| Browser interaction | 2 | Stop, these don't work for React |
| Total | 15 | Submit report regardless of completeness |

**If you exceed 15 iterations, your report may be incomplete. That's OK.** Judge can do its own code review.

---

## Common Traps

### Trap 1: "Let me try one more click method"

You've tried `click()`, `dispatchEvent`, `MouseEvent`, `PointerEvent`. None worked. **Stop.** React events cannot be triggered this way. Move on.

### Trap 2: "The button click didn't work, so the feature is broken"

No. The feature code is correct. Your test method is incompatible with React. Check the source code instead.

### Trap 3: "I need to verify EVERY feature in the browser"

No. Code review is sufficient for 80% of features. Browser tests are for:
- Verifying DOM structure
- Checking visual rendering (canvas, etc.)
- Native event handlers (vanilla JS projects)

### Trap 4: "I'll modify the code to make it testable"

**NEVER suggest code changes.** Your job is to evaluate, not fix. If code is untestable via browser but logically correct, report "CODE PASS" with browser limitation note.

---

## Quick Reference: Feature → Verification Method

| Feature Type | Primary Method | Browser Test |
|-------------|---------------|-------------|
| Text input + submit | Code review: check handlers | ❌ Not possible |
| Button click action | Code review: check onClick | ❌ Not possible |
| Data persistence | Code review: check localStorage/DB | ❌ Not possible |
| List rendering | Code review: check map() | DOM existence check |
| Modal/dialog | Code review: check CSS visibility | DOM existence check |
| Canvas drawing | Code review: check canvas API | Pixel data check |
| Animation | Code review: check CSS/JS | Visual screenshot |
| Responsive layout | Code review: check media queries | Screenshot at sizes |

---

## Emergency Checklist

If you're stuck:
- [ ] Have you read the source code for this feature?
- [ ] Can you verify it works by code inspection?
- [ ] Have you tried >2 browser test methods?
- [ ] Are you testing React controlled components?
- [ ] Have you exceeded 15 total iterations?

**If any of the last 3 are YES → STOP and submit your report.**
