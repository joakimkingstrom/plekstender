import { useMe, useSetPreferences } from '../api/hooks'
import { useReducedMotion, useRoomForBackgrounds } from '../motion'
import { useTheme, type ThemeChoice } from '../theme'
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

/** Light, dark or the system's theme, and album covers behind the pages (off, covers, or one
 * colour's) with their speed: in the header's menu and on the profile. A theme chosen here is
 * this browser's, and the account's too when it keeps one (Profile → Theme). */
export function ThemeOptions() {
  const me = useMe()
  const save = useSetPreferences()
  const reduced = useReducedMotion()
  const room = useRoomForBackgrounds()
  const [, setChoice, choice] = useTheme()
  const background: Required<BackgroundSettings> = { ...DEFAULT, ...me.data?.preferences?.background }
  const set = (patch: Partial<BackgroundSettings>) => save.mutate({ background: { ...background, ...patch } })
  const choose = (next: ThemeChoice) => {
    setChoice(next)
    // The account's too when it keeps one; the system's: this browser follows its device.
    if (me.data?.preferences?.theme) save.mutate({ theme: next === 'system' ? null : next })
  }
  return (
    <div className="space-y-4 text-sm">
      <div className="space-y-1.5">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted">Theme</p>
        <Segmented
          label="Theme"
          value={choice}
          onChange={choose}
          options={[
            ['light', 'Light'],
            ['dark', 'Dark'],
            ['system', 'System'],
          ]}
          className="w-full"
        />
      </div>
      {/* Not on phones: the page covers nearly all of the covers there. */}
      {room && (
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
      )}
    </div>
  )
}
