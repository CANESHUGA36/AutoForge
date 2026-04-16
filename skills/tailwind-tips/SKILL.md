---
description: Tailwind CSS utilities, color tokens, and responsive design tips
---

# Tailwind Tips

## 1. Custom colors in config
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

## 2. Dark mode with `class`
Set `darkMode: 'class'` in config. Toggle dark mode by adding/removing `dark` class on `<html>` or a wrapper element.

## 3. Use `group` and `peer` for relative styling
```html
<div class="group">
  <div class="group-hover:bg-gray-100">Hover the parent</div>
</div>
```

## 4. Mobile-first responsive design
Tailwind defaults to mobile-first. Use `md:` and `lg:` prefixes for larger breakpoints.

## 5. Arbitrary values sparingly
Prefer extending the theme over arbitrary values like `w-[123px]`. Arbitrary values hurt consistency.
