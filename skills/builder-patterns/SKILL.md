---
description: Builder anti-patterns and best practices. How to avoid common traps that waste iterations, break builds, or cause 0% scores. Read this BEFORE writing code.
---

# Builder Patterns & Anti-Patterns

> **Read this skill BEFORE you start coding.** It will save you from the most common failure modes that cause 0% scores or wasted iterations.

---

## Anti-Pattern 1: Conditional Rendering (Causes 0%)

### The Trap

You write `{condition && <Element />}` to hide elements when not needed. Reviewer uses `document.querySelector()` to find elements. If condition is false, element doesn't exist in DOM → Reviewer marks FAIL → **0% score**.

### The Rule

**Always use CSS to control visibility. Never use conditional rendering.**

```tsx
// ❌ WRONG — element not in DOM when audioUrl is null
{audioUrl && <div className="spectrum-container">...</div>}
{showModal && <Modal>...</Modal>}
{tasks.length > 0 && <TaskList tasks={tasks} />}
{isLoading && <LoadingSpinner />}
{error && <ErrorMessage error={error} />}
```

```tsx
// ✅ CORRECT — element always in DOM, CSS controls visibility
<div className="spectrum-container" style={{display: audioUrl ? 'flex' : 'none'}}>
  ...
</div>

<div className="modal" style={{display: showModal ? 'block' : 'none'}}>
  ...
</div>

<div className="task-list" style={{display: tasks.length > 0 ? 'block' : 'none'}}>
  ...
</div>

<div className="loading" style={{visibility: isLoading ? 'visible' : 'hidden'}}>
  ...
</div>

<div className="error" style={{opacity: error ? 1 : 0, pointerEvents: error ? 'auto' : 'none'}}>
  ...
</div>
```

### Pre-Submit Self-Check (Mandatory)

Before declaring done, run:
```bash
grep -rn "&&\s*<" src/
grep -rn "?\s*<.*>\s*:" src/
```
If any matches found, fix them before submitting.

---

## Anti-Pattern 2: Browser Check Addiction (Wastes 20+ Iterations)

### The Trap

You write code, then repeatedly call `browser_check` to "verify" it works. You try to click buttons, fill inputs, check state changes. Each check takes 10-15 seconds. You burn 20+ iterations on verification that doesn't actually validate anything meaningful.

### What You CANNOT Test via browser_check

| Feature | Why It Fails | What To Do Instead |
|---------|-------------|-------------------|
| React button `onClick` | `element.click()` doesn't trigger React synthetic events | Code review: check handler is bound |
| React input `onChange` | Programmatic events bypass React event system | Code review: check onChange handler |
| Zustand/Redux state update | Can't observe internal state from browser | Code review: check action creators |
| Form submission | React intercepts submit events | Code review: check onSubmit handler |

### What You CAN Test via browser_check

```javascript
// ✅ Check DOM existence (reliable)
return {
  hasButton: !!document.querySelector('[data-testid="f1-btn"]'),
  buttonText: document.querySelector('[data-testid="f1-btn"]')?.textContent,
  hasCanvas: !!document.querySelector('canvas'),
  hasInput: !!document.querySelector('input[type="text"]'),
};
```

### The Rule

**Maximum 2 browser_check calls per feature group.**

```
1st check: Verify DOM structure (elements exist, correct data-testid)
2nd check: If 1st failed, fix code and verify once more
After 2 checks: STOP. Submit. Let Reviewer do its job.
```

**`validate_build()` passing is the highest-confidence evidence that code is correct.** Do not doubt it because browser_check shows something unexpected.

---

## Anti-Pattern 3: Starting Dev Server Yourself

### The Trap

You see "localhost not reachable" and think "I need to start the dev server." You run `npm run dev &` in background. But:
- Background processes get killed when command timeout expires
- You create port conflicts with the framework-managed dev server
- You waste 3-5 iterations on server management instead of coding

### The Rule

**NEVER start the dev server yourself.**

- The framework starts dev server BEFORE Builder begins
- `browser_check` handles server availability automatically
- If `browser_check` fails with "localhost unreachable", it's an environment issue, not your code
- **Stop trying, submit the code**

---

## Anti-Pattern 4: Chasing CSS Warnings

### The Trap

You see `[CSS WARNING] Missing classes: bg-background, text-primary` and spend 5 iterations trying to fix Tailwind config.

### The Reality

These warnings are **non-blocking**. The build succeeds. The app works. Tailwind v4 uses CSS variables, not traditional utility classes. These warnings are false positives from the build validator.

### The Rule

**Ignore CSS warnings if build passes.** Only fix CSS if:
- Build actually fails
- Visual layout is broken (verified by screenshot)
- Specific components are missing styles

---

## Anti-Pattern 5: Over-Engineering State Management

### The Trap

You create complex Zustand stores with 20+ actions, middleware, persistence. Then you try to expose the store on `window` for browser testing. Then you remove it. Then you add it back. 10 iterations wasted.

### The Rule

**Keep state simple.**

```tsx
// ✅ Simple — enough for 95% of projects
import { create } from 'zustand';

const useStore = create((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
}));
```

```tsx
// ❌ Over-engineered — wastes iterations
const useStore = create(
  persist(
    (set, get) => ({
      count: 0,
      increment: () => {
        const newCount = get().count + 1;
        set({ count: newCount });
        window.__storeActions?.onIncrement?.(newCount);
      },
    }),
    { name: 'store' }
  )
);
```

**Never expose store on `window` for testing.** It's a code smell and wastes iterations.

---

## Anti-Pattern 6: The "One More Fix" Loop

### The Trap

You submit code, then think "wait, I should also fix that border color." Then "the padding looks off." Then "I should add a hover effect." 15 iterations later, you've changed 50 files and introduced 3 new bugs.

### The Rule

**If build passes and core functionality is implemented, STOP.**

- Iteration budget is 50 max
- If you've used >40 iterations, only fix blocking bugs
- If you've used >30 iterations, stop adding polish
- **Submit working code > Perfect code that never gets submitted**

---

## Anti-Pattern 7: Ignoring the Spec

### The Trap

You skim the spec, start coding based on assumptions, build something that looks cool but doesn't match requirements. Reviewer checks against spec → FAIL.

### The Rule

**Read spec.md carefully before writing any code.**

Focus on:
1. **Feature groups** (F1, F2, D1, T1) — these are the scoring criteria
2. **data-testid attributes** — Reviewer looks for these specifically
3. **Technical stack** — use the specified template
4. **Functional requirements** — what must the app DO, not how it looks

---

## Best Practices Summary

| Practice | Why It Matters |
|----------|---------------|
| CSS visibility > conditional rendering | Prevents 0% scores |
| Max 2 browser_check calls | Saves 15+ iterations |
| Don't start dev server | Avoids port conflicts |
| Ignore CSS warnings (if build passes) | Saves 5+ iterations |
| Simple state management | Reduces bug surface |
| Stop when build passes | Ensures submission |
| Read spec first | Builds correct features |

---

## Emergency Escape Hatch

If you find yourself in any of these situations:
- >5 iterations on the same bug → **Declare PIVOT strategy**
- >10 iterations on browser_check → **Stop, submit, move on**
- >40 total iterations → **Stop adding features, only fix crashes**
- Build passes but you're still "verifying" → **SUBMIT NOW**
