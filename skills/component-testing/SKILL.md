---
description: Pre-submission checklist for Builder to self-verify before finishing a round. Catches 80% of common issues before Evaluator sees them.
---

# Component Testing Checklist

Run through this checklist BEFORE committing and finishing your round.

## Build Verification

```bash
# 1. Type check
npx tsc --noEmit

# 2. Production build
npm run build

# 3. Dev server runtime verification (CRITICAL)
# Start dev server in background
npm run dev &
sleep 15  # Wait for first compilation

# HTTP health check — MUST return 200
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000)
echo "HTTP Status: $HTTP_CODE"

# Content check — MUST contain expected title
TITLE=$(curl -s http://localhost:3000 | grep -o "<title>.*</title>")
echo "Page title: $TITLE"

# Stop dev server
pkill -f "next dev"
```

**If HTTP code is not 200, or title is missing/contains "404"/"error":**
1. Check `cat .next/trace 2>/dev/null | tail -20` for build errors
2. Read `build-troubleshooting` skill
3. Fix the issue — DO NOT commit

**Common causes of 404/500 after successful build:**
- Google Fonts download failure (socket hang up) → Remove problematic fonts
- Corrupted `.next` cache → `rm -rf .next` and rebuild
- Missing error components → Clear cache and rebuild

**If build fails**: Read `build-troubleshooting` skill FIRST before asking for help.

## Visual Checklist

### Color System
- [ ] All colors use CSS variables or Tailwind config tokens
- [ ] No hardcoded hex values scattered in components
- [ ] Dark/light mode works (if applicable)

### Typography
- [ ] Font explicitly loaded (Google Fonts link or next/font)
- [ ] Heading font different from body font
- [ ] Size scale: heading > subheading > body > caption

### Spacing
- [ ] Uses consistent spacing scale (4/8/12/16/24/32/48/64)
- [ ] No arbitrary values like `margin: 7px`
- [ ] Touch targets >= 44x44px

### Components
- [ ] Hover states on ALL interactive elements
- [ ] Focus states visible (keyboard navigation)
- [ ] Active/pressed states on buttons
- [ ] Disabled states styled (if applicable)
- [ ] Loading states handled
- [ ] Empty states handled

## Functional Checklist

### Interactive Elements
- [ ] Buttons are clickable and do something
- [ ] Forms validate input
- [ ] Navigation links work
- [ ] Modal/dialog opens and closes

### State Management
- [ ] State updates trigger re-render
- [ ] No stale closures (functions reference current state)
- [ ] localStorage/IndexedDB persistence works (if applicable)

### Edge Cases
- [ ] Empty input handled
- [ ] Long text doesn't break layout
- [ ] Rapid clicking doesn't crash
- [ ] Mobile viewport works (375px)
- [ ] Desktop viewport works (1280px)

## Animation Checklist

- [ ] Animations respect `prefers-reduced-motion`
- [ ] No janky transitions (smooth 200-300ms)
- [ ] Hover effects feel responsive
- [ ] Scroll animations trigger correctly

## Accessibility Checklist

- [ ] All images have `alt` text
- [ ] Interactive elements have `aria-label` if no visible text
- [ ] Color contrast >= 4.5:1
- [ ] Keyboard navigation works (Tab, Enter, Escape)
- [ ] Focus indicators visible

## Code Quality Checklist

- [ ] No `console.log` left in production code
- [ ] No `TODO` or `FIXME` comments
- [ ] No unused imports
- [ ] No unused variables
- [ ] Types defined (TypeScript)
- [ ] Props typed correctly

## Asset Checklist

- [ ] All images referenced in code exist on disk
- [ ] No broken image links
- [ ] Icons load correctly
- [ ] Fonts load correctly

## Final Steps

1. Run `npm run build` one more time
2. Start dev server and verify with curl (see Build Verification section above)
3. Verify HTTP 200 and correct page title/content
4. Resize browser to test responsive breakpoints
5. Check browser console for errors
6. Run through keyboard navigation (Tab key)
7. Commit with `git add -A && git commit -m "round N: description"`
