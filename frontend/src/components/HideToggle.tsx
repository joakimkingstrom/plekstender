import { Tooltip } from './Tooltip'
import { Button } from './ui'

/** Hide or show something again, as an eye button: crossed out while it's shown (hide it),
 * open once it's hidden (show it again). */
export function HideToggle({
  hidden,
  name,
  busy = false,
  onToggle,
}: {
  hidden: boolean
  /** What's hidden or shown, for the button's name ("Hide Karma Police"). */
  name: string
  busy?: boolean
  onToggle: () => void
}) {
  return (
    <Tooltip content={hidden ? 'Show it again' : 'Hide it'}>
      <Button variant="ghost" size="xs" busy={busy} onClick={onToggle} aria-label={`${hidden ? 'Show' : 'Hide'} ${name}`}>
        <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
          <path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          <circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
          {!hidden && <path d="M2.5 13.5l11-11" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />}
        </svg>
      </Button>
    </Tooltip>
  )
}
