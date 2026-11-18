import { useEffect, useState } from 'react'

type Theme = 'dark' | 'light'

function readTheme(): Theme {
  try {
    return localStorage.getItem('theme') === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('theme', theme)
    } catch {
      // storage unavailable (private mode); theme just won't persist
    }
  }, [theme])
  return [theme, setTheme] as const
}
