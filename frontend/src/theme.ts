import { useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'
/** What the person chose: a theme, or the system's (light or dark, following the device). */
export type ThemeChoice = Theme | 'system'

const LIGHT = '(prefers-color-scheme: light)'

function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem('theme')
    return stored === 'light' || stored === 'system' ? stored : 'dark'
  } catch {
    return 'dark'
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.(LIGHT).matches ? 'light' : 'dark'
}

/** The theme in use, the choice behind it, and a way to change the choice (kept in this
 * browser). "System" follows the device's light or dark mode, also as it changes. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice)
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
  useEffect(() => {
    try {
      localStorage.setItem('theme', choice)
    } catch {
      // storage unavailable (private mode); the choice just won't persist
    }
  }, [choice])
  return [theme, setChoice, choice] as const
}
