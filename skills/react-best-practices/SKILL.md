---
description: React performance patterns and common pitfalls to avoid
---

# React Best Practices

## 1. Avoid creating new functions in render
Creating inline callbacks or objects in JSX causes unnecessary re-renders of child components.

**Bad:**
```tsx
<button onClick={() => setCount(c => c + 1)}>+</button>
```

**Good:**
```tsx
const increment = useCallback(() => setCount(c => c + 1), []);
<button onClick={increment}>+</button>
```

## 2. Keep state minimal and co-located
Only lift state up when truly necessary. Prefer `useReducer` for complex state logic.

## 3. Use `key` correctly
Always provide stable, unique keys when rendering lists. Avoid using array index as key when order can change.

## 4. Memoize expensive computations
```tsx
const filtered = useMemo(() => items.filter(predicate), [items, predicate]);
```

## 5. Clean up side effects
Always return cleanup functions in `useEffect` for subscriptions, timers, and event listeners.
