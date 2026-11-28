import { useEffect, useId, useRef, useState } from 'react'
import { useTheme } from '../theme'
import { ThemeOptions } from './ThemeOptions'

/** The light/dark button, as a menu: the theme and the album covers behind the pages
 * (ThemeOptions, as on the profile). */
export function ThemeMenu() {
  const [theme] = useTheme()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const id = useId()
  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={id}
        aria-label="Theme and background"
        title="Theme and background"
        onClick={() => setOpen((o) => !o)}
        className="rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-text"
      >
        {theme === 'dark' ? '☾' : '☀'}
      </button>
      {open && (
        <div
          id={id}
          role="dialog"
          aria-label="Theme and background"
          className="absolute right-0 top-full z-30 mt-2 w-72 space-y-4 rounded-2xl border border-border bg-surface p-4 text-sm shadow-lg"
        >
          <ThemeOptions />
        </div>
      )}
    </div>
  )
}
