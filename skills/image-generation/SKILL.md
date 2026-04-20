---
description: Best practices for using generate_image tool with MiniMax image-01 API. How to write effective prompts and handle common issues.
---

# Image Generation Guide

## Prompt Engineering

### Structure: Subject + Style + Lighting + Colors + Mood

```
[Subject], [Art Style], [Lighting], [Color Palette], [Mood/Atmosphere]
```

### Examples by Use Case

**Hero Banner (16:9)**:
```
A serene mountain landscape at golden hour, oil painting style, warm orange and purple sky with dramatic clouds, soft diffused lighting from the setting sun, rich earth tones with teal accents, peaceful and contemplative mood
```

**Character Portrait (1:1)**:
```
A young female warrior with silver hair and piercing blue eyes, anime art style, dramatic rim lighting from behind, dark blue and silver color palette with crimson accents, fierce and determined expression
```

**Background/Texture (16:9)**:
```
Abstract geometric pattern with interlocking hexagons, digital art style, neon glow lighting, deep navy blue with electric cyan accents, futuristic and high-tech atmosphere
```

**Icon/Logo (1:1)**:
```
A minimalist shield emblem with a stylized flame, flat vector art style, clean even lighting, monochromatic gold on dark background, professional and trustworthy feeling
```

## Technical Rules

### File Paths
- Always use `.jpg` or `.jpeg` extension (API returns JPEG)
- Save to `assets/` or `public/` directory
- Use descriptive filenames:
  ```
  assets/hero-banner.jpg
  assets/character-protagonist.jpg
  assets/background-forest.jpg
  assets/icon-settings.jpg
  ```

### Aspect Ratios
| Use Case | Ratio | Dimensions |
|----------|-------|------------|
| Hero banner | 16:9 | 1280x720 |
| Character portrait | 1:1 | 512x512 |
| Mobile background | 9:16 | 720x1280 |
| Icon/Avatar | 1:1 | 256x256 |
| Card thumbnail | 4:3 | 640x480 |

### Batch Generation

When a page needs multiple images, generate them in sequence:

```typescript
// 1. Generate all assets FIRST
const images = [
  { prompt: "...", path: "assets/hero.jpg", ratio: "16:9" },
  { prompt: "...", path: "assets/char1.jpg", ratio: "1:1" },
  { prompt: "...", path: "assets/char2.jpg", ratio: "1:1" },
]

for (const img of images) {
  generate_image(img.prompt, img.path, img.ratio)
}

// 2. Verify they exist
list_files("assets")

// 3. THEN write the UI code
```

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| "API key not set" | MINIMAX_API_KEY missing | Check .env file |
| "Non-JSON response" | Server error | Retry with shorter prompt |
| Image too generic | Prompt too vague | Add specific style, lighting, mood |
| Wrong colors | No color palette specified | Include "color palette: X and Y" |
| Blurry details | Subject not specific enough | Describe specific features |

## Anti-Patterns

```
// Bad: Too short
"A mountain landscape"

// Bad: No style specified
"A character portrait"

// Bad: Wrong file extension
"assets/hero.png"  // API returns JPEG!

// Bad: No mood/atmosphere
"A city skyline, digital art"
```

## Integration with Next.js

```tsx
import Image from 'next/image'
import heroImg from './assets/hero.jpg'

// Local image (imported)
<Image src={heroImg} alt="Hero" priority className="w-full" />

// Or from public/
// <Image src="/assets/hero.jpg" alt="Hero" width={1280} height={720} />
```

## Integration with Vite

```tsx
// Vite handles imports automatically
import heroImg from './assets/hero.jpg'

<img src={heroImg} alt="Hero" className="w-full" />
```
