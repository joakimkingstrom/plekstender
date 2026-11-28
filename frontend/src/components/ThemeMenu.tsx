import { useEffect, useId, useRef, useState } from 'react'
import { useMe, useSetPreferences } from '../api/hooks'
import type { Theme, ThemeChoice } from '../theme'
import { useReducedMotion } from '../motion'
import type { BackgroundSettings } from './CoverBackground'
import { Segmented } from './Segmented'

type Family = NonNullable<BackgroundSettings['family']>
/** The colour families (as Library → Stats → Colours groups covers), with a swatch each. */
const FAMILIES: [Family, string, string][] = [
  ['red', 'Red', '#d64545'],
  ['orange', 'Orange', '#e8873a'],
  ['yellow', 'Yellow', '#e6c84a'],
  ['green', 'Green', '#4caf6a'],
  ['teal', 'Teal', '#2fa6a0'],
  ['blue', 'Blue', '#3f7fd9'],
  ['purple', 'Purple', '#8a5cd1'],
  ['pink', 'Pink', '#d65ca5'],
  ['white', 'White', '#f2f0ea'],
  ['grey', 'Grey', '#8b8a85'],
  ['black', 'Black', '#1c1d21'],
]
const DEFAULT: Required<BackgroundSettings> = { mode: 'off', family: 'blue', speed: 'normal' }

/** The light/dark button, as a menu: light, dark or the system's, and album covers behind
 * the pages (off, covers, or one colour's), with their speed. */
export function ThemeMenu({
  theme,
  choice,
  onChoose,
}: {
  theme: Theme
  choice: ThemeChoice
  onChoose: (choice: ThemeChoice) => void
}) {
  const me = useMe()
  const save = useSetPreferences()
  const reduced = useReducedMotion()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const id = useId()
  const background: Required<BackgroundSettings> = { ...DEFAULT, ...me.data?.preferences?.background }
  const set = (patch: Partial<BackgroundSettings>) => save.mutate({ background: { ...background, ...patch } })

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
          <div className="space-y-1.5">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">Theme</p>
            <Segmented
              label="Theme"
              value={choice}
              onChange={onChoose}
              options={[
                ['light', 'Light'],
                ['dark', 'Dark'],
                ['system', 'System'],
              ]}
              className="w-full"
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">Background</p>
            <Segmented
              label="Background"
              value={background.mode}
              onChange={(mode) => set({ mode })}
              options={[
                ['off', 'Off'],
                ['covers', 'Covers'],
                ['colour', 'Colour'],
              ]}
              className="w-full"
            />
            {background.mode === 'covers' && (
              <p className="text-xs text-muted">Light covers in light mode, dark ones in dark mode.</p>
            )}
            {background.mode === 'colour' && (
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Colour">
                {FAMILIES.map(([key, label, swatch]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={background.family === key}
                    aria-label={label}
                    title={label}
                    onClick={() => set({ family: key })}
                    className={`size-6 rounded-full ring-1 ring-border ${background.family === key ? 'ring-2 ring-text ring-offset-2 ring-offset-surface' : ''}`}
                    style={{ background: swatch }}
                  />
                ))}
              </div>
            )}
            {background.mode !== 'off' && (
              <Segmented
                label="Speed"
                value={background.speed}
                onChange={(speed) => set({ speed })}
                options={[
                  ['slow', 'Slow'],
                  ['normal', 'Normal'],
                  ['fast', 'Fast'],
                ]}
                size="sm"
                className="w-full"
              />
            )}
            {background.mode !== 'off' && reduced && (
              <p className="text-xs text-muted">Your system asks for less motion, so no covers are shown.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
