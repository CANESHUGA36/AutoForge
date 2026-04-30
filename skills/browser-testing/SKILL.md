---
description: Browser automation testing guide for Reviewer agents. Covers React controlled component limitations, what can/cannot be tested, file upload simulation, and when to use code review instead.
---

# Browser Testing Guide

> **Read this skill BEFORE running browser tests.** It will save you from wasting iterations on impossible tests.

## Core Principle: Code Review > Browser Tests

**Browser tests are supplementary. Code review is primary.**

If you can verify functionality by reading the source code, **do not waste iterations on browser automation**.

---

## Part 1: What CANNOT Be Tested (Don't Waste Time)

### React Controlled Components — Impossible to Trigger Programmatically

React inputs with `value={state} onChange={setState}` **cannot** be filled by JavaScript event dispatch.

```tsx
// This is a controlled component — IMPOSSIBLE to test via browser automation
<input
  value={inputValue}
  onChange={(e) => setInputValue(e.target.value)}
  data-testid="f1-task-input"
/>
```

**All of these methods FAIL:**
```javascript
// ❌ FAIL — JavaScript events don't trigger React synthetic handlers
input.value = "text";
input.dispatchEvent(new Event('input'));

// ❌ FAIL — Keyboard events don't update React state
input.dispatchEvent(new KeyboardEvent('keypress', { key: 'a' }));

// ❌ FAIL — InputEvent also doesn't work
input.dispatchEvent(new InputEvent('input', { data: 'text' }));

// ❌ FAIL — execCommand is deprecated and doesn't work with React
document.execCommand('insertText', false, 'text');

// ❌ FAIL — Clipboard API requires user permission
navigator.clipboard.writeText('text');

// ❌ FAIL — React Fiber manipulation is fragile and unreliable
const fiber = input.__reactFiber$...;
```

**Why they fail:** React intercepts events at the root level using its synthetic event system. Programmatically dispatched events bypass React's event pooling, so `onChange` never fires and state never updates.

### What This Means for Testing

| Feature | Can Browser Test? | What To Do Instead |
|---------|------------------|-------------------|
| Text input + Enter submit | ❌ No | Code review: check onKeyDown handler |
| Text input + button submit | ❌ No | Code review: check onClick handler |
| Form validation messages | ⚠️ Partial | Code review: check validation logic |
| File upload via `<input type="file">` | ✅ Yes (see Part 3) | Use upload simulation |
| Drag & drop file upload | ✅ Yes | Use DataTransfer simulation |
| **React button onClick** | ❌ No | `element.click()` doesn't trigger React handlers |
| **React state update (Zustand/Redux)** | ❌ No | Internal state not observable from DOM |
| Native button clicks | ✅ Yes | Use click action |
| Checkbox toggles | ✅ Yes | Use click action |
| Select dropdown | ✅ Yes | Use click + click option |

---

## Part 2: What CAN Be Tested

### Button Clicks

```javascript
browser_check(
  url="http://localhost:5173",
  mode="interact",
  fresh=true,
  actions=[
    {type: "click", selector: "[data-testid='f1-add-button']"},
    {type: "wait", delay: 500}
  ]
)
```

### Fill Text Input (Playwright CDP Level)

**This works because it uses Chrome DevTools Protocol, not JavaScript events:**

```javascript
browser_check(
  url="http://localhost:5173",
  mode="interact",
  fresh=true,
  actions=[
    {type: "fill", selector: "[data-testid='f1-task-input']", value: "Buy groceries"},
    {type: "click", selector: "[data-testid='f1-add-button']"},
    {type: "wait", delay: 500}
  ]
)
```

**Important:** The `fill` action only works in `mode="interact"`, not in `mode="inspect"` with `script`.

### File Upload Simulation

```javascript
browser_check(
  url="http://localhost:5173",
  mode="interact",
  fresh=true,
  actions=[
    {type: "evaluate", script: `
      const input = document.querySelector('input[type="file"]');
      const file = new File([''], 'test.mp3', { type: 'audio/mpeg' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return { uploaded: true };
    `},
    {type: "wait", delay: 1000}
  ]
)
```

### Drag & Drop Simulation

```javascript
browser_check(
  url="http://localhost:5173",
  mode="interact",
  fresh=true,
  actions=[
    {type: "evaluate", script: `
      const dropzone = document.querySelector('.dropzone');
      const file = new File([''], 'test.mp3', { type: 'audio/mpeg' });
      const dt = new DataTransfer();
      dt.items.add(file);
      dropzone.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true }));
      return { dropped: true };
    `},
    {type: "wait", delay: 1000}
  ]
)
```

### CSS Force-Show for DOM Structure Validation

When elements are CSS-hidden (`display: none`), you can force-show them to verify DOM structure:

```javascript
browser_check(
  url="http://localhost:5173",
  mode="inspect",
  fresh=true,
  script: `
    const el = document.querySelector('[data-testid="f3-control-bar"]');
    if (el) {
      el.style.display = 'flex';
      el.style.visibility = 'visible';
      el.style.opacity = '1';
    }
    return {
      exists: !!el,
      childCount: el ? el.children.length : 0,
      hasPlayBtn: !!el?.querySelector('[data-testid="f3-play-btn"]'),
      hasVolumeSlider: !!el?.querySelector('[data-testid="f3-volume-slider"]')
    };
  `
)
```

---

## Part 3: File Upload & Audio App Testing Strategy

### The Problem

Music/audio apps typically have this flow:
1. User uploads audio file
2. App processes audio (Web Audio API)
3. Playback controls appear
4. Visualizations render

**Steps 1-2 require real file data.** You cannot test the full flow.

### The Solution: Test in Layers

**Layer 1: Code Review (Primary)**
```
✅ Check: File input exists with onChange handler
✅ Check: Handler calls URL.createObjectURL() or FileReader
✅ Check: AudioContext initialization logic exists
✅ Check: Playback controls are CSS-hidden (not conditionally rendered)
✅ Check: Visualization canvas exists in DOM
→ PASS — code logic is correct
```

**Layer 2: DOM Structure Validation (Secondary)**
```javascript
// Force-show controls to verify DOM structure
browser_check(
  mode="inspect",
  fresh=true,
  script: `
    // Force show all hidden elements
    document.querySelectorAll('[data-testid]').forEach(el => {
      el.style.display = el.style.display === 'none' ? 'block' : el.style.display;
    });
    return {
      hasUploadZone: !!document.querySelector('[data-testid="f1-upload-zone"]'),
      hasControlBar: !!document.querySelector('[data-testid="f2-control-bar"]'),
      hasPlayBtn: !!document.querySelector('[data-testid="f2-play-btn"]'),
      hasCanvas: !!document.querySelector('[data-testid="f3-waveform-canvas"]')
    };
  `
)
```

**Layer 3: File Upload Simulation (If Needed)**
```javascript
// Only if the app uses standard file input
browser_check(
  mode="interact",
  fresh=true,
  actions=[
    {type: "evaluate", script: `
      const input = document.querySelector('input[type="file"]');
      if (!input) return { hasFileInput: false };
      const file = new File([''], 'test.mp3', { type: 'audio/mpeg' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return { hasFileInput: true, triggered: true };
    `},
    {type: "wait", delay: 2000}
  ]
)
```

**Layer 4: Console Error Check**
```javascript
browser_check(
  mode="inspect",
  fresh=true,
  script: `
    return {
      consoleErrors: (() => {
        // Check for React errors, audio API errors, etc.
        const errors = [];
        // browser_check returns console_errors automatically
        return errors;
      })()
    };
  `
)
```

### When to STOP Testing and Rely on Code Review

**STOP if:**
- You've tried 2 browser tests and they don't trigger the expected state change
- The feature requires real audio/video file processing
- The app uses complex Web Audio API / Canvas rendering
- React controlled components are involved

**Code review is sufficient when:**
- Event handlers are non-empty functions
- State management logic is correct
- DOM elements exist (CSS-hidden is OK)
- No console errors on initial load

---

## Part 4: browser_check Action Reference

### Action Types (mode="interact")

| Action | Purpose | Works with React? |
|--------|---------|-------------------|
| `click` | Click element | ✅ Yes |
| `fill` | Type text into input | ✅ Yes (CDP level) |
| `wait` | Pause for N ms | ✅ Yes |
| `scroll` | Scroll page | ✅ Yes |
| `evaluate` | Run JavaScript | ✅ Yes (for non-input operations) |

### Mode Selection Guide

| Goal | Mode | Why |
|------|------|-----|
| Check element exists | `inspect` | Fast, no interaction needed |
| Click buttons | `interact` | Uses Playwright CDP |
| Type text in input | `interact` with `fill` action | CDP injection works |
| Run JavaScript query | `inspect` with `script` | Direct eval |
| File upload | `interact` with `evaluate` | Manual file object creation |
| Take screenshot | `screenshot` | Visual verification |

---

## Part 5: Common Mistakes to Avoid

### ❌ Mistake 1: Using `mode="inspect"` with `script` to type text
```javascript
// WRONG — This will NOT update React state
browser_check(
  mode="inspect",
  script: "input.value = 'text'; input.dispatchEvent(new Event('input'));"
)
```

### ✅ Correct: Use `mode="interact"` with `fill` action
```javascript
browser_check(
  mode="interact",
  actions=[
    {type: "fill", selector: "#input", value: "text"}
  ]
)
```

### ❌ Mistake 2: Trying to trigger React state with JavaScript events
```javascript
// WRONG — All of these fail for React controlled components
input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));
input.dispatchEvent(new InputEvent('input', {data: 'text'}));
document.execCommand('insertText', false, 'text');
```

### ✅ Correct: Check code instead
```javascript
// Read source code to verify handler exists
read_file(path="src/App.tsx");
// Check: onKeyDown={handleSubmit} exists and handleSubmit is non-empty
```

### ❌ Mistake 3: Spending >5 iterations on browser tests
If browser tests aren't working after 2-3 attempts, **switch to code review**.

### ❌ Mistake 4: Trying to trigger React button clicks via JavaScript
Builder agents often waste 10+ iterations trying:
```javascript
document.querySelector('[data-testid="btn"]').click();
document.querySelector('[data-testid="btn"]').dispatchEvent(new MouseEvent('click'));
```
**These NEVER work for React onClick handlers.** Stop after 1 attempt.

### ✅ Correct: Budget your iterations
- Max 3 browser_check calls per review
- Max 2 browser_check calls for Builder
- If all fail → rely on code review
- Document in report: "Browser automation limitation — verified via code review"

---

## Quick Decision Tree

```
Start Review
│
├─→ Read source files (max 5 files)
│   └─→ Check: elements exist? handlers non-empty? CSS hidden?
│
├─→ Is feature testable via browser?
│   ├─→ Button click / checkbox / select? → browser_check (interact mode)
│   ├─→ Text input in React? → Code review only (fill action if needed)
│   ├─→ File upload? → Try evaluate action with DataTransfer
│   ├─→ Audio/Canvas/WebGL? → Code review + DOM structure check
│   └─→ Conditional rendering? → Code review + CSS force-show
│
└─→ Write report
    ├─→ PASS: "Code verified: handler exists, state logic correct"
    └─→ FAIL: "Element missing from source" / "Handler is empty function"
```
