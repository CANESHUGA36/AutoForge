---
description: Comprehensive accessibility checklist for web UI components. Covers keyboard navigation, ARIA, contrast, screen readers, and reduced motion.
---

# Accessibility Checklist

## 1. Keyboard Navigation

- All interactive elements must be focusable and actionable via keyboard
- Tab order follows visual order (left-to-right, top-to-bottom)
- Trap focus inside modals and allow `Escape` to close them
- Provide skip links for main content

```tsx
// Good: Native elements are keyboard accessible by default
<button onClick={handleClick}>Submit</button>
<a href="/about">About</a>

// Bad: Div pretending to be a button
<div onClick={handleClick}>Submit</div>  // NOT keyboard accessible!

// Fix: Add tabindex, role, and keyboard handler
<div
  role="button"
  tabIndex={0}
  onClick={handleClick}
  onKeyDown={(e) => e.key === 'Enter' && handleClick()}
>
  Submit
</div>
```

## 2. ARIA Attributes

### When to use ARIA
- **Do use**: When semantic HTML doesn't convey enough information
- **Don't use**: As a substitute for semantic HTML

```tsx
// Good: Semantic HTML
<nav>
  <ul>
    <li><a href="/">Home</a></li>
  </ul>
</nav>

// Good: ARIA for dynamic content
<button aria-expanded={isOpen} aria-controls="menu">
  Menu
</button>
<div id="menu" role="region" aria-label="Navigation menu">
  {/* menu items */}
</div>

// Good: ARIA for icon buttons
<button aria-label="Close dialog">
  <XIcon />
</button>
```

### Common ARIA Patterns

```tsx
// Modal/Dialog
<div role="dialog" aria-modal="true" aria-labelledby="dialog-title">
  <h2 id="dialog-title">Confirm Action</h2>
  {/* content */}
</div>

// Tabs
<div role="tablist">
  <button role="tab" aria-selected={true} aria-controls="panel-1">Tab 1</button>
  <button role="tab" aria-selected={false} aria-controls="panel-2">Tab 2</button>
</div>
<div role="tabpanel" id="panel-1">{/* content */}</div>

// Live regions for announcements
<div aria-live="polite" aria-atomic="true">
  {notification}
</div>

// Form errors
<input aria-invalid={hasError} aria-describedby="error-msg" />
<span id="error-msg" role="alert">{errorMessage}</span>
```

## 3. Color Contrast

- Text must have contrast ratio >= 4.5:1 against background
- Large text (18px+ bold or 24px+) >= 3:1
- Do not rely on color alone to convey information

```tsx
// Bad: Only color indicates error
<span style={{ color: 'red' }}>Invalid</span>

// Good: Color + icon + text
<span style={{ color: '#dc2626' }}>
  <ErrorIcon aria-hidden="true" /> Invalid email format
</span>
```

## 4. Screen Reader Announcements

```tsx
// Dynamic updates
const [announcement, setAnnouncement] = useState('')

function handleComplete() {
  setAnnouncement('Timer complete! Time for a break.')
}

return (
  <>
    <div aria-live="polite" className="sr-only">
      {announcement}
    </div>
    {/* main content */}
  </>
)
```

```css
/* Visually hidden but screen-reader accessible */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

## 5. Reduced Motion

```css
/* Respect user preference */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

```tsx
// Framer Motion
import { motion, useReducedMotion } from 'framer-motion'

function AnimatedComponent() {
  const shouldReduceMotion = useReducedMotion()
  
  return (
    <motion.div
      initial={shouldReduceMotion ? false : { opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: shouldReduceMotion ? 0 : 0.3 }}
    >
      Content
    </motion.div>
  )
}
```

## 6. Form Labels

```tsx
// Good: Explicit label
<label htmlFor="email">Email address</label>
<input id="email" type="email" />

// Good: Implicit label
<label>
  Email address
  <input type="email" />
</label>

// Good: aria-label when visible label isn't possible
<input type="search" aria-label="Search products" />

// Good: aria-labelledby
<span id="search-label">Search</span>
<input type="search" aria-labelledby="search-label" />
```

## 7. Images

```tsx
// Good: Descriptive alt text
<img src="chart.png" alt="Bar chart showing sales increased 25% in Q3" />

// Good: Decorative image hidden from screen readers
<img src="decoration.png" alt="" role="presentation" />

// Good: Complex image with detailed description
<figure>
  <img src="diagram.png" alt="System architecture diagram" />
  <figcaption>Figure 1: The system consists of three layers...</figcaption>
</figure>
```

## 8. Focus Management

```tsx
// Focus trap for modal
import { useRef, useEffect } from 'react'

function Modal({ isOpen, onClose, children }) {
  const modalRef = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    if (isOpen && modalRef.current) {
      modalRef.current.focus()
    }
  }, [isOpen])
  
  if (!isOpen) return null
  
  return (
    <div
      ref={modalRef}
      role="dialog"
      tabIndex={-1}
      aria-modal="true"
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
    >
      {children}
    </div>
  )
}
```

## Quick Reference: ARIA Roles

| Element | Use Instead |
|---------|------------|
| `<div role="button">` | `<button>` |
| `<div role="link">` | `<a>` |
| `<div role="heading">` | `<h1>` - `<h6>` |
| `<div role="list">` | `<ul>` or `<ol>` |
| `<div role="listitem">` | `<li>` |
| `<div role="navigation">` | `<nav>` |
| `<div role="main">` | `<main>` |
| `<div role="article">` | `<article>` |
