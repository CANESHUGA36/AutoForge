---
description: Frontend design quality rubric covering Design Quality, Originality, and Craft with per-band scoring guides and anti-patterns
---

# Frontend Design Principles

This skill is used by three agents:
- **Planner**: use Part 1 to write a concrete visual design direction in spec.md
- **Builder**: use Part 2 to implement with quality and avoid common pitfalls
- **Evaluator**: use Part 3 for per-dimension scoring rubrics when grading

---

## Part 1 — Visual Design Language (for Planner)

When writing the design direction in spec.md, define all five of the following. Vague directions like "clean and modern" are not acceptable.

### 1. Color System
Define a palette with roles, not just hex values:
- **Primary action color** — used for CTAs, highlights, progress indicators
- **Background tones** — 1-2 surface colors (never plain white `#ffffff` unless intentional)
- **Text hierarchy** — at least 3 levels (heading, body, muted)
- **Semantic colors** — success, warning, danger

Good example:
> Primary: `#E84855` (coral red). Backgrounds: `#1A1A2E` (dark navy) / `#16213E` (card surface). Text: `#EAEAEA` / `#A8A8B3` / `#5A5A72`. Accent: `#F5A623` for running state.

Bad example:
> "Use a nice color palette with purple and white."

### 2. Typography
- Specify a heading font and a body font (Google Fonts or system stack)
- Define size scale: heading, subheading, body, caption (in rem or px)
- Define weight usage: bold for headings, medium for labels, regular for body

### 3. Spacing & Layout
- Choose a base unit (e.g., 4px or 8px grid)
- Define the overall layout approach: centered card, full-bleed, sidebar, etc.
- Specify padding/gap rhythm (e.g., "8px inner padding, 16px between sections, 32px page margin")

### 4. Component Style
Pick ONE visual style and stick to it:
- **Glassmorphism**: frosted glass effect, `backdrop-filter: blur()`, semi-transparent borders
- **Neumorphism**: soft shadows in/out, monochrome surfaces
- **Flat + accent**: solid fills, no gradients, one vivid accent color
- **Brutalist**: high contrast, raw borders, intentional asymmetry
- **Minimal**: lots of whitespace, thin strokes, muted palette

### 5. Motion & Interaction States
- Specify transition duration and easing (e.g., `200ms ease-out` for micro-interactions)
- Define hover, active, focus, and disabled visual states
- List any meaningful animations (e.g., progress ring draw, button pulse on timer tick)

---

## Part 2 — Implementation Guidelines (for Builder)

### Must-haves for any web app
- Every interactive element has a **visible hover AND focus state** (not just color change — also transform or outline)
- Button click gives **immediate visual feedback** (active state, min 100ms)
- Disabled states look and behave disabled (`cursor: not-allowed`, reduced opacity, no pointer events)
- Loading/empty states are handled — never leave a blank space while data loads
- Font is explicitly loaded or declared — never rely on browser default serif

### Typography rules
```css
/* Always set these on :root or body */
font-family: 'YourFont', system-ui, sans-serif;
-webkit-font-smoothing: antialiased;
line-height: 1.5;        /* body text */
letter-spacing: -0.01em; /* headings only */
```

### Color anti-patterns to avoid
- **Do NOT** use `background: linear-gradient(135deg, #667eea, #764ba2)` — this is the #1 AI-default cliche
- **Do NOT** use pure white `#fff` background with purple/blue cards — overused template aesthetic
- **Do NOT** use `box-shadow: 0 10px 30px rgba(0,0,0,0.1)` on every card — lazy depth
- **Do NOT** mix more than 3 hue families in one UI

### Spacing rules
- Use a consistent spacing scale. Recommended: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px`
- Never use arbitrary pixel values like `margin: 7px` or `padding: 13px`
- Minimum touch target size: `44x44px` for any clickable element

### CSS architecture
- Use CSS custom properties (`--color-primary`, `--spacing-md`) for all design tokens
- Group related styles; keep selector specificity flat
- Prefer `gap` over `margin` for flex/grid children

---

## Part 3 — Scoring Rubrics (for Evaluator)

Use these rubrics to assign scores to each dimension. Be a **skeptical QA**, not a lenient reviewer. Default-looking AI output scores 4-5, not 7-8.

---

### Dimension A: Design Quality (weight: HIGH)
*Does the UI have a unified, intentional visual identity?*

| Score | Description |
|-------|-------------|
| 9-10 | Strong, distinctive visual system. Color, type, spacing, and component style all reinforce a single clear aesthetic. Would look at home in a real product portfolio. |
| 7-8 | Clear visual direction with minor inconsistencies. Most elements feel intentional. One or two elements feel out of place. |
| 5-6 | Some design choices present, but identity is fragmented. Mix of styles or templates. Feels "assembled" rather than designed. |
| 3-4 | Generic template look. Purple/blue gradient + white cards, or Bootstrap defaults. No original design decisions. |
| 1-2 | Completely unstyled or near-default browser styles. No evident visual intent. |

**Checklist:**
- [ ] Color palette is defined and consistently applied (not random hex values)
- [ ] Typography has clear hierarchy (heading vs. body vs. label are visually distinct)
- [ ] Spacing is consistent — not random padding values throughout
- [ ] Every component uses the same visual language (border-radius, shadow, etc.)

---

### Dimension B: Originality (weight: HIGH)
*Are there custom design decisions, or is this AI-default aesthetics?*

**Known AI-default patterns that indicate low originality (each deducts points):**
- `linear-gradient(135deg, #667eea 0%, #764ba2 100%)` or similar purple gradients
- Pure white background + cards with `box-shadow: 0 10px 30px rgba(0,0,0,0.1)`
- Emoji icons as the primary visual language (e.g., ⏱️ as the "logo")
- Generic sans-serif with no font loading (falling back to system default)
- Green = success, Red = error with no other palette considered
- Rounded pill buttons in gradient colors

| Score | Description |
|-------|-------------|
| 9-10 | Distinctive aesthetic that feels intentionally crafted for this specific app's personality. Palette, type, and style choices feel unique and considered. |
| 7-8 | Some original choices. At least 2-3 design decisions that deviate meaningfully from template defaults. |
| 5-6 | Mostly template-like, but at least one clear custom decision (e.g., custom color scheme, unusual layout). |
| 3-4 | Standard AI-default aesthetic. Purple gradients, white cards, system fonts. Nothing that couldn't be from a generic template. |
| 1-2 | Appears to be copied directly from a starter template with no customization. |

---

### Dimension C: Craft (weight: MEDIUM)
*Is the technical execution precise and polished?*

| Score | Description |
|-------|-------------|
| 9-10 | Pixel-precise spacing, perfect color harmony, smooth transitions, every state handled (hover, active, focus, disabled, empty, loading). No visual jank. |
| 7-8 | Good execution with 1-2 minor craft issues (e.g., slightly inconsistent spacing, one missing hover state). |
| 5-6 | Several craft issues. Spacing inconsistent in places, some states not styled, transitions missing or jarring. |
| 3-4 | Rough execution. Obvious misalignments, missing states, broken layouts at some viewport sizes. |
| 1-2 | Broken or unfinished styling. Elements overlap, layout collapses, or styles partially applied. |

**Craft checklist:**
- [ ] Hover states on all buttons and interactive elements
- [ ] Focus states visible for keyboard navigation
- [ ] Active/pressed states on buttons
- [ ] Disabled states properly styled where applicable
- [ ] Consistent border-radius across all components
- [ ] No text overflow issues (long strings don't break layout)
- [ ] Responsive at both 375px (mobile) and 1280px (desktop) widths
- [ ] Transitions feel smooth (not instant, not sluggish)

---

### Dimension D: Functionality (weight: HIGH)
*Does every feature work as specified in contract.md?*

| Score | Description |
|-------|-------------|
| 9-10 | All contracted features work correctly. Edge cases handled. No console errors. |
| 7-8 | Core features all work. 1-2 minor issues (e.g., visual glitch, one edge case unhandled). |
| 5-6 | Most features work but 1-2 significant functional gaps or bugs that affect usability. |
| 3-4 | Core feature partially works or has a blocking bug. Several secondary features missing. |
| 1-2 | Core feature broken or not implemented. App is not usable. |

**Testing procedure:**
1. For each functional criterion in contract.md, perform the exact test steps described
2. Note any console errors (`browser_evaluate("JSON.stringify(window.__errors || [])")`)
3. Test edge cases: empty inputs, rapid clicking, long strings, timer at 0
4. Verify state persistence: does refreshing break the app state?

---

## Scoring Summary Template

When writing feedback.md, always use this format for the score summary:

```
SCORE: X/10

Design Quality: X/10  [PASS/FAIL if below threshold]
Originality:    X/10  [PASS/FAIL if below threshold]
Craft:          X/10  [PASS/FAIL if below threshold]
Functionality:  X/10  [PASS/FAIL if below threshold]
```

A sprint FAILS if ANY dimension is below its threshold, regardless of total score.
Hard thresholds: Functionality ≥ 5 | Design Quality ≥ 4 | Originality ≥ 3 | Craft ≥ 3
