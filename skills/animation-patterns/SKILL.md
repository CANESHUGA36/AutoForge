---
description: CSS and JS animation patterns for web UIs. Includes typewriter, parallax, hover effects, scroll reveals, and particle systems.
---

# Animation Patterns

## Typewriter Effect

### Requirement: Per-character span generation (for DOM inspection)

```tsx
"use client"
import { motion } from 'framer-motion'

export function Typewriter({ text, delay = 0.06 }: { text: string; delay?: number }) {
  return (
    <p>
      {text.split('').map((char, i) => (
        <motion.span
          key={i}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: i * delay, duration: 0.01 }}
        >
          {char}
        </motion.span>
      ))}
    </p>
  )
}
```

**Critical**: Each character MUST be a separate `<span>`. Do NOT use string state updates.

## Parallax Effect

```tsx
"use client"
import { useRef, useState } from 'react'

export function ParallaxContainer({ children }: { children: React.ReactNode }) {
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const ref = useRef<HTMLDivElement>(null)

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const x = (e.clientX - rect.left - rect.width / 2) / 20
    const y = (e.clientY - rect.top - rect.height / 2) / 20
    setOffset({ x, y })
  }

  return (
    <div ref={ref} onMouseMove={handleMouseMove} className="relative overflow-hidden">
      <div style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}>
        {children}
      </div>
    </div>
  )
}
```

## 3D Card Hover

```tsx
"use client"
import { useRef, useState } from 'react'

export function Card3D({ children }: { children: React.ReactNode }) {
  const [rotate, setRotate] = useState({ x: 0, y: 0 })
  const ref = useRef<HTMLDivElement>(null)

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const x = ((e.clientY - rect.top) / rect.height - 0.5) * -10
    const y = ((e.clientX - rect.left) / rect.width - 0.5) * 10
    setRotate({ x, y })
  }

  const handleMouseLeave = () => setRotate({ x: 0, y: 0 })

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        perspective: '1000px',
        transform: `rotateX(${rotate.x}deg) rotateY(${rotate.y}deg)`,
        transition: 'transform 0.3s ease-out',
      }}
    >
      {children}
    </div>
  )
}
```

## Scroll Reveal (Framer Motion)

```tsx
"use client"
import { motion, useInView } from 'framer-motion'
import { useRef } from 'react'

export function ScrollReveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const ref = useRef(null)
  const isInView = useInView(ref, { once: true, margin: "-100px" })

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 50 }}
      animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 50 }}
      transition={{ duration: 0.6, delay, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  )
}
```

## Staggered Children Animation

```tsx
"use client"
import { motion } from 'framer-motion'

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.15 },
  },
}

const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
}

export function StaggerList({ children }: { children: React.ReactNode[] }) {
  return (
    <motion.div variants={container} initial="hidden" animate="show">
      {children.map((child, i) => (
        <motion.div key={i} variants={item}>{child}</motion.div>
      ))}
    </motion.div>
  )
}
```

## Particle System (CSS-only)

```tsx
export function Particles({ count = 30 }: { count?: number }) {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="absolute rounded-full bg-cyan-400"
          style={{
            width: `${4 + Math.random() * 8}px`,
            height: `${4 + Math.random() * 8}px`,
            left: `${Math.random() * 100}%`,
            top: `${Math.random() * 100}%`,
            opacity: 0.3 + Math.random() * 0.4,
            animation: `float ${5 + Math.random() * 3}s ease-in-out infinite`,
            animationDelay: `${Math.random() * 5}s`,
          }}
        />
      ))}
    </div>
  )
}
```

```css
@keyframes float {
  0%, 100% { transform: translateY(0) translateX(0); }
  25% { transform: translateY(-20px) translateX(10px); }
  50% { transform: translateY(-10px) translateX(-10px); }
  75% { transform: translateY(-30px) translateX(5px); }
}
```

## Glow Pulse Animation

```css
@keyframes glow-pulse {
  0%, 100% { box-shadow: 0 0 20px rgba(0, 240, 255, 0.3); }
  50% { box-shadow: 0 0 40px rgba(0, 240, 255, 0.6); }
}

.glow-pulse {
  animation: glow-pulse 3s ease-in-out infinite;
}
```

## Reduced Motion Support

```css
/* Always include this */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

## Stat Bar Fill Animation

```tsx
"use client"
import { motion, useInView } from 'framer-motion'
import { useRef } from 'react'

export function StatBar({ label, value }: { label: string; value: number }) {
  const ref = useRef(null)
  const isInView = useInView(ref, { once: true })

  return (
    <div ref={ref} className="mb-2">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span>{value}%</span>
      </div>
      <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
        <motion.div
          className="h-full bg-cyan-400 rounded-full"
          initial={{ width: 0 }}
          animate={isInView ? { width: `${value}%` } : { width: 0 }}
          transition={{ duration: 1, ease: "easeOut" }}
        />
      </div>
    </div>
  )
}
```
