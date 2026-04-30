---
description: Common build errors in React/Next.js/Vite projects and their fixes. Read this BEFORE running npm run build to avoid known pitfalls.
---

# Build Troubleshooting

Read this skill when `npm run build` fails or before starting a new project to avoid common build errors.

## Next.js App Router

### FAIL: Cannot find module for page: /_document

**Cause**: App Router does NOT use `_document.tsx`. That file is for Pages Router only.

**Fix**: Remove `_document.tsx`. Use `src/app/layout.tsx` instead:
```tsx
// src/app/layout.tsx
export const metadata = {
  title: 'My App',
  description: '...',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
```

### FAIL: "use client" directive error

**Cause**: Client component imported in Server Component without "use client".

**Fix**: Add `"use client"` at the VERY TOP of any file using:
- `useState`, `useEffect`, `useRef`
- `useRouter` from `next/navigation`
- Browser APIs (`window`, `document`, `localStorage`)
- Event handlers (`onClick`, `onChange`)

```tsx
"use client"  // Must be first line
import { useState } from 'react'
```

### FAIL: `next/font/google` import error

**Cause**: Using `next/font` incorrectly in App Router.

**Fix**:
```tsx
import { Inter } from 'next/font/google'

const inter = Inter({ subsets: ['latin'] })

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  )
}
```

## TypeScript Errors

### CRITICAL: Build fails on unused imports or variables (TS6133 / TS6196)

**Cause**: `tsconfig.json` has `noUnusedLocals: true` or `noUnusedParameters: true`.

**Fix**: When creating or editing `tsconfig.json` (or `tsconfig.app.json`), ALWAYS set these to `false`:
```json
{
  "compilerOptions": {
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true
  }
}
```
This prevents build failures from minor refactoring artifacts (unused imports, temporary variables) while keeping real type safety (`strict: true`).

**Rule**: If you see `TS6133` or `TS6196` errors, edit tsconfig to disable these two flags instead of fixing each unused variable individually.

### FAIL: Cannot find module 'xxx' or its corresponding type declarations

**Fix 1**: Install missing types:
```bash
npm install -D @types/xxx
```

**Fix 2**: Add declaration to `src/types/global.d.ts`:
```typescript
declare module 'xxx'
```

### FAIL: Type 'Element' is not assignable to type 'ReactNode'

**Cause**: Component return type mismatch.

**Fix**: Ensure component returns valid JSX:
```tsx
// Bad: returns string directly
function Greeting({ name }) { return `Hello ${name}` }

// Good: wraps in JSX
function Greeting({ name }: { name: string }) { return <span>Hello {name}</span> }
```

### FAIL: Property 'xxx' does not exist on type 'JSX.IntrinsicElements'

**Cause**: Using non-standard HTML attributes.

**Fix**: Use `data-*` attributes or extend JSX:
```tsx
// Bad: <div customAttr="value">
// Good: <div data-custom-attr="value">
```

## Vite Build Errors

### FAIL: "@" path alias not resolved

**Fix**: Ensure `tsconfig.json` and `vite.config.ts` both define aliases:
```typescript
// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
```

```json
// tsconfig.json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  }
}
```

### FAIL: Top-level await is not available

**Fix**: Update `vite.config.ts`:
```typescript
export default defineConfig({
  build: {
    target: 'esnext',  // or 'es2022'
  },
})
```

## Tailwind CSS Errors

### FAIL: Tailwind classes not working in production

**Checklist**:
1. `tailwind.config.js` has correct `content` paths:
```javascript
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx}',
    './public/index.html',
  ],
}
```
2. `postcss.config.js` exists:
```javascript
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```
3. CSS file has directives:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### FAIL: Arbitrary values not working

**Cause**: Tailwind doesn't recognize dynamic class names.

**Fix**: Use full class strings, not concatenation:
```tsx
// Bad: className={`w-[${width}px]`}
// Good: className="w-[100px]"

// Bad: className={`grid-cols-${cols}`}
// Good: className="grid-cols-3"
```

## General Build Fixes

### Clear cache and rebuild
```bash
rm -rf .next dist node_modules/.vite
npm run build
```

### Check for syntax errors before building
```bash
npx tsc --noEmit  # Type check only, no emit
```

### Common missing dependencies
```bash
# If build fails with module not found
npm install  # Reinstall all dependencies
```

## Builder-Specific Build Rules

### Rule 1: validate_build() passing = code is correct
If `validate_build()` returns `[BUILD OK]`, your code is correct. **Do not** doubt it because browser_check shows something unexpected.

### Rule 2: CSS warnings are non-blocking
```
[CSS WARNING] Missing classes: bg-background, text-primary, border-primary. Check Tailwind/CSS config. (Non-blocking)
```
**Ignore these.** Tailwind v4 uses CSS variables, not traditional utility classes. These warnings are false positives.

### Rule 3: Do NOT run npm install, npm ci, npm update
These are handled by the framework. Running them wastes iterations and may break the environment.

### Rule 4: Do NOT modify tsconfig to fix unused variable warnings
Instead, set these in tsconfig:
```json
{
  "compilerOptions": {
    "noUnusedLocals": false,
    "noUnusedParameters": false
  }
}
```
This prevents build failures from refactoring artifacts while keeping real type safety.
