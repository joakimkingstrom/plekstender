import { useId } from 'react'
import { PICK_PATH, STAR_PATH } from '../brand'

/**
 * The guitar-pick mark (branding/plekstender-mark.svg): the pick takes `currentColor`
 * (the text colour by default) and holds a half-filled star in `--color-mark-star`.
 * Add the `pick-spin` class to make it spin (the loading animation).
 */
export function Mark({ className = 'size-6' }: { className?: string }) {
  const clipId = `mark-half-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <clipPath id={clipId}>
        <rect x="-8" y="-8" width="40" height="80" />
      </clipPath>
      <path fill="currentColor" d={PICK_PATH} />
      <path
        fill="none"
        stroke="var(--color-mark-star)"
        strokeWidth={2.4}
        strokeLinejoin="miter"
        strokeMiterlimit={4}
        d={STAR_PATH}
      />
      <path fill="var(--color-mark-star)" clipPath={`url(#${clipId})`} d={STAR_PATH} />
    </svg>
  )
}
