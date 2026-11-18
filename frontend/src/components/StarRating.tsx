import { useState, type KeyboardEvent } from 'react'

const STAR = 'M10 1.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L10 14.9l-5.2 2.7 1-5.8L1.5 7.7l5.9-.9z'

function Star({ fill, size }: { fill: number; size: string }) {
  // fill: 0, 0.5 or 1
  return (
    <svg viewBox="0 0 20 20" className={size} aria-hidden>
      <path d={STAR} fill="currentColor" opacity={0.2} />
      {fill > 0 && (
        <path d={STAR} fill="currentColor" style={fill < 1 ? { clipPath: 'inset(0 50% 0 0)' } : undefined} />
      )}
    </svg>
  )
}

/**
 * Interactive 5-star rating with half stars, on Plex's 0-10 scale. Click the left or right
 * half of a star; clicking the current rating clears it. Keyboard: arrows step by half a
 * star, 1-5 set whole stars (Shift for the half below), 0 / Delete clears.
 */
export function StarRating({
  value,
  onChange,
  label,
  size = 'size-5',
  disabled = false,
}: {
  value: number | null | undefined
  onChange: (value: number | null) => void
  label: string
  size?: string
  disabled?: boolean
}) {
  const [hover, setHover] = useState<number | null>(null)
  const shown = hover ?? value ?? 0

  const set = (next: number | null) => {
    if (disabled) return
    onChange(next !== null && next === value ? null : next)
  }

  const onKey = (e: KeyboardEvent) => {
    const current = value ?? 0
    let next: number | null | undefined
    if (e.key === 'ArrowRight') next = Math.min(10, current + 1)
    else if (e.key === 'ArrowLeft') next = Math.max(1, current - 1)
    else if (/^[1-5]$/.test(e.key)) next = Number(e.key) * 2 - (e.shiftKey ? 1 : 0)
    else if (e.key === '0' || e.key === 'Delete' || e.key === 'Backspace') next = null
    if (next !== undefined) {
      e.preventDefault()
      if (!disabled) onChange(next)
    }
  }

  return (
    <span
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={5}
      aria-valuenow={(value ?? 0) / 2}
      aria-valuetext={value ? `${value / 2} out of 5 stars` : 'Not rated'}
      aria-disabled={disabled || undefined}
      onKeyDown={onKey}
      onMouseLeave={() => setHover(null)}
      className={`inline-flex shrink-0 items-center text-accent focus-visible:rounded focus-visible:outline-2 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}
    >
      {[1, 2, 3, 4, 5].map((star) => {
        const fill = Math.max(0, Math.min(1, (shown - (star - 1) * 2) / 2))
        return (
          <span key={star} className="relative inline-flex">
            <Star fill={fill >= 1 ? 1 : fill >= 0.5 ? 0.5 : 0} size={size} />
            {/* Two hit areas per star: left = half, right = whole. */}
            <span
              className="absolute inset-y-0 left-0 w-1/2"
              onMouseEnter={() => setHover(star * 2 - 1)}
              onClick={() => set(star * 2 - 1)}
            />
            <span
              className="absolute inset-y-0 right-0 w-1/2"
              onMouseEnter={() => setHover(star * 2)}
              onClick={() => set(star * 2)}
            />
          </span>
        )
      })}
    </span>
  )
}
