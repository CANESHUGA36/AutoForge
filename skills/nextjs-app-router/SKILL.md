---
description: Next.js 14 App Router patterns, file conventions, and common mistakes. Essential for any Next.js project.
---

# Next.js App Router Guide

## File Structure (App Router)

```
src/app/
├── layout.tsx       # Root layout (REQUIRED) - wraps ALL pages
├── page.tsx         # Home page at /
├── globals.css      # Global styles
├── loading.tsx      # Loading UI (optional)
├── error.tsx        # Error boundary (optional)
├── not-found.tsx    # 404 page (optional)
├── about/
│   └── page.tsx     # /about
├── blog/
│   ├── page.tsx     # /blog
│   └── [slug]/
│       └── page.tsx # /blog/:slug
```

## Critical Rules

### 1. Every route needs `page.tsx`

```tsx
// src/app/about/page.tsx
export default function AboutPage() {
  return <h1>About</h1>
}
```

### 2. Layout wraps children

```tsx
// src/app/layout.tsx
export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
```

### 3. Server Components by default

All components in App Router are Server Components unless marked with `"use client"`.

**Use `"use client"` when you need:**
- React hooks (`useState`, `useEffect`)
- Browser APIs (`window`, `document`, `localStorage`)
- Event handlers
- Client-side libraries (Framer Motion, GSAP)

```tsx
"use client"
import { useState } from 'react'

export default function Counter() {
  const [count, setCount] = useState(0)
  return <button onClick={() => setCount(c => c + 1)}>{count}</button>
}
```

### 4. Fonts: Use `next/font`

```tsx
import { Inter, Cinzel } from 'next/font/google'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const cinzel = Cinzel({ subsets: ['latin'], variable: '--font-cinzel' })

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${inter.variable} ${cinzel.variable}`}>
      <body className={inter.className}>{children}</body>
    </html>
  )
}
```

Then in CSS/Tailwind:
```css
font-family: var(--font-cinzel), serif;
```

### 5. Images: Use `next/image`

```tsx
import Image from 'next/image'

// Local image
import heroImg from './assets/hero.jpg'

export default function Hero() {
  return (
    <Image
      src={heroImg}
      alt="Hero"
      priority  // Load immediately (above fold)
      className="w-full h-auto"
    />
  )
}

// Remote image (add to next.config.js)
export default function Avatar() {
  return (
    <Image
      src="https://example.com/avatar.jpg"
      alt="Avatar"
      width={64}
      height={64}
    />
  )
}
```

```javascript
// next.config.js
module.exports = {
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'example.com' },
    ],
  },
}
```

### 6. Metadata API

```tsx
import { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'My App',
  description: 'Description',
  openGraph: {
    title: 'My App',
    images: ['/og-image.jpg'],
  },
}
```

### 7. Route Groups (for layout organization)

```
src/app/
├── (marketing)/     # Group - no URL segment
│   ├── layout.tsx   # Marketing layout
│   ├── page.tsx     # /
│   └── about/
│       └── page.tsx # /about
└── (dashboard)/     # Another group
    ├── layout.tsx   # Dashboard layout
    └── dashboard/
        └── page.tsx # /dashboard
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using `useRouter` from `next/router` | Use `useRouter` from `next/navigation` |
| Creating `_document.tsx` | Delete it. Use `layout.tsx` |
| Using `getServerSideProps` | Use Server Components directly |
| Using `<Head>` from `next/head` | Use `metadata` export or `next/head` in client components |
| Putting `window` check in render | Use `useEffect` or dynamic import with `ssr: false` |

## Dynamic Routes

```tsx
// src/app/blog/[slug]/page.tsx
export default function BlogPost({ params }: { params: { slug: string } }) {
  return <h1>Post: {params.slug}</h1>
}

// Generate static params at build time
export async function generateStaticParams() {
  return [
    { slug: 'hello-world' },
    { slug: 'another-post' },
  ]
}
```
