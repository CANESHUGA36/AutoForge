---
name: react-ecosystem
description: React best practices, Tailwind CSS utilities, color tokens, and responsive design tips
---

# React Ecosystem Best Practices

## React Performance

### 1. Avoid creating new functions in render
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

### 2. Keep state minimal and co-located
Only lift state up when truly necessary. Prefer `useReducer` for complex state logic.

### 3. Use `key` correctly
Always provide stable, unique keys when rendering lists. Avoid using array index as key when order can change.

### 4. Memoize expensive computations
```tsx
const filtered = useMemo(() => items.filter(predicate), [items, predicate]);
```

### 5. Clean up side effects
Always return cleanup functions in `useEffect` for subscriptions, timers, and event listeners.

## Tailwind CSS

### 1. Custom colors in config
Define design-system colors in `tailwind.config.js` under `theme.extend.colors`:

```js
colors: {
  'brand': {
    light: '#E17055',
    dark: '#FF7675',
  }
}
```

Then use: `text-brand-light`, `bg-brand-dark`.

### 2. Dark mode with `class`
Set `darkMode: 'class'` in config. Toggle dark mode by adding/removing `dark` class on `<html>` or a wrapper element.

### 3. Use `group` and `peer` for relative styling
```html
<div class="group">
  <div class="group-hover:bg-gray-100">Hover the parent</div>
</div>
```

### 4. Mobile-first responsive design
Tailwind defaults to mobile-first. Use `md:` and `lg:` prefixes for larger breakpoints.

### 5. Arbitrary values sparingly
Prefer extending the theme over arbitrary values like `w-[123px]`. Arbitrary values hurt consistency.
