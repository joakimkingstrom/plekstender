import { useState, type ReactNode } from 'react'
import { useReducedMotion } from '../motion'

/**
 * A cover that plays its video cover (muted, looping) while the pointer is over it, laid over
 * the still one. Never on its own (so long lists stay still), and not at all when the
 * system asks for less motion.
 */
export function VideoCover({ video, children, className = '' }: { video: string | null | undefined; children: ReactNode; className?: string }) {
  const reduced = useReducedMotion()
  const [hover, setHover] = useState(false)
  if (!video) return <>{children}</>
  return (
    <span
      className={`relative block ${className}`}
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => setHover(false)}
    >
      {children}
      {hover && !reduced && (
        <video
          src={video}
          muted
          loop
          autoPlay
          playsInline
          aria-hidden
          data-testid="video-cover"
          className="pointer-events-none absolute inset-0 size-full rounded-[inherit] object-cover"
        />
      )}
      <span className="pointer-events-none absolute bottom-1 right-1 rounded bg-black/55 px-1 text-[10px] font-semibold uppercase tracking-wide text-white" aria-label="Has a video cover">
        ▶
      </span>
    </span>
  )
}
