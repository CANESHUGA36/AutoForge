---
description: Accessibility checklist for web UI components
---

# Accessibility Checklist

## 1. Keyboard navigation
- All interactive elements must be focusable and actionable via keyboard.
- Trap focus inside modals and allow `Escape` to close them.

## 2. ARIA attributes
- Use `role`, `aria-label`, `aria-live`, and `aria-describedby` appropriately.
- Do not use ARIA as a substitute for semantic HTML.

## 3. Color contrast
- Text should have a contrast ratio of at least 4.5:1 against its background.
- Do not rely on color alone to convey information.

## 4. Screen reader announcements
- Dynamically update `aria-live` regions for important state changes (e.g., timer completion).
- Hide purely decorative elements with `aria-hidden="true"`.

## 5. Reduced motion
- Respect `prefers-reduced-motion` by disabling or toning down animations:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

## 6. Form labels
- Every input must have an associated `<label>` or `aria-label`.
- Provide clear error messages and associate them with inputs using `aria-describedby`.
