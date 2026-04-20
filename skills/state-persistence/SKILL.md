---
description: State persistence patterns for React apps using localStorage, IndexedDB, and Zustand. Prevents data loss on refresh.
---

# State Persistence Guide

## localStorage (Simple Data)

### Pattern: Zustand + localStorage

```typescript
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface SettingsState {
  theme: 'light' | 'dark' | 'system'
  workDuration: number
  breakDuration: number
  setTheme: (theme: string) => void
  setWorkDuration: (min: number) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'system',
      workDuration: 25,
      breakDuration: 5,
      setTheme: (theme) => set({ theme }),
      setWorkDuration: (min) => set({ workDuration: min }),
    }),
    {
      name: 'settings-storage',
    }
  )
)
```

### Pattern: Manual localStorage

```typescript
function useLocalStorage<T>(key: string, initialValue: T) {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === 'undefined') return initialValue
    try {
      const item = window.localStorage.getItem(key)
      return item ? JSON.parse(item) : initialValue
    } catch {
      return initialValue
    }
  })

  const setStoredValue = (newValue: T) => {
    setValue(newValue)
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(key, JSON.stringify(newValue))
    }
  }

  return [value, setStoredValue] as const
}
```

## IndexedDB (Complex Data)

### Pattern: Dexie.js for Session History

```typescript
import Dexie from 'dexie'

interface Session {
  id?: number
  startTime: Date
  duration: number
  completed: boolean
  mode: string
}

class AppDatabase extends Dexie {
  sessions!: Dexie.Table<Session, number>

  constructor() {
    super('AppDatabase')
    this.version(1).stores({
      sessions: '++id, startTime, mode',
    })
  }
}

export const db = new AppDatabase()

// Usage
async function saveSession(session: Omit<Session, 'id'>) {
  return await db.sessions.add(session)
}

async function getTodaySessions() {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return await db.sessions
    .where('startTime')
    .above(today)
    .toArray()
}
```

## Timer State Persistence (Critical Pattern)

### What to persist vs not persist

```typescript
interface TimerState {
  // PERSIST: User preferences
  workDuration: number      // Save
  breakDuration: number     // Save
  longBreakDuration: number // Save
  soundEnabled: boolean     // Save
  
  // DO NOT PERSIST: Runtime state
  isRunning: boolean        // Don't save - fresh start should be paused
  timeRemaining: number     // Don't save - reset on refresh
  phase: 'work' | 'break'   // Optional: save if you want resume
}

export const useTimerStore = create<TimerState>()(
  persist(
    (set) => ({
      workDuration: 25,
      breakDuration: 5,
      longBreakDuration: 15,
      soundEnabled: true,
      isRunning: false,
      timeRemaining: 25 * 60,
      phase: 'work',
    }),
    {
      name: 'timer-storage',
      // Only persist preferences, not runtime state
      partialize: (state) => ({
        workDuration: state.workDuration,
        breakDuration: state.breakDuration,
        longBreakDuration: state.longBreakDuration,
        soundEnabled: state.soundEnabled,
      }),
    }
  )
)
```

## Common Persistence Mistakes

| Mistake | Fix |
|---------|-----|
| `window.localStorage` in Server Component | Use `"use client"` or check `typeof window !== 'undefined'` |
| Storing entire state object | Use `partialize` to only persist user preferences |
| Not handling JSON parse errors | Wrap in try/catch with fallback to default |
| Storing Dates as strings | Dates become strings after JSON.stringify. Reconstruct: `new Date(stored)` |
| Storing functions or class instances | Only plain objects/arrays/primitives can be JSON serialized |

## Data Export Pattern

```typescript
export async function exportData(): Promise<string> {
  const sessions = await db.sessions.toArray()
  const settings = JSON.parse(localStorage.getItem('settings-storage') || '{}')
  
  const exportData = {
    version: '1.0',
    exportedAt: new Date().toISOString(),
    sessions,
    settings: settings.state || {},
  }
  
  return JSON.stringify(exportData, null, 2)
}

export function downloadExport(data: string, filename = 'export.json') {
  const blob = new Blob([data], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
```
