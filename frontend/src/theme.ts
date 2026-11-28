import { useEffect, useState, useSyncExternalStore } from 'react'

export type Theme = 'dark' | 'light'
/** What the person chose: a theme, or the system's (light or dark, following the device). */
export type ThemeChoice = Theme | 'system'

const LIGHT = '(prefers-color-scheme: light)'

function systemTheme(): Theme {
  return window.matchMedia?.(LIGHT).matches ? 'light' : 'dark'
}

// The choice, shared by everything that shows or changes it (the header's menu, the profile):
// this browser's storage, or what was last chosen when storage isn't available.
let unstored: ThemeChoice = 'dark'
const listeners = new Set<() => void>()

function chosen(): ThemeChoice {
  try {
    const stored = localStorage.getItem('theme')
    return stored === 'light' || stored === 'system' || stored === 'dark' ? stored : unstored
  } catch {
    return unstored
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function choose(next: ThemeChoice) {
  try {
    localStorage.setItem('theme', next)
  } catch {
    // storage unavailable (private mode): kept for this page only
    unstored = next
  }
  listeners.forEach((listener) => listener())
}

/** The theme in use, the choice behind it, and a way to change the choice (kept in this
 * browser, and the same everywhere it's used). "System" follows the device's light or dark
 * mode, also as it changes. */
export function useTheme() {
  const choice = useSyncExternalStore(subscribe, chosen)
  const [system, setSystem] = useState<Theme>(systemTheme)
  useEffect(() => {
    const query = window.matchMedia?.(LIGHT)
    if (!query?.addEventListener) return
    const changed = () => setSystem(query.matches ? 'light' : 'dark')
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [])
  const theme: Theme = choice === 'system' ? system : choice
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  return [theme, choose, choice] as const
}
