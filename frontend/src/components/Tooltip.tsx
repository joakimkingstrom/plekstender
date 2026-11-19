import { cloneElement, isValidElement, useId, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const DELAY = 250
const GAP = 6
const EDGE = 8

/**
 * A site-styled tooltip for whatever it wraps: shown on hover (after a short delay) and
 * on keyboard focus, placed above (or below, near the top of the screen) and kept on
 * screen. The text is linked with aria-describedby, so screen readers announce it too.
 */
export function Tooltip({
  content,
  children,
  className = '',
}: {
  content: ReactNode
  children: ReactNode
  className?: string
}) {
  const id = useId()
  const anchor = useRef<HTMLSpanElement>(null)
  const bubble = useRef<HTMLDivElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const [open, setOpen] = useState(false)

  const show = (delay: number) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOpen(true), delay)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    setOpen(false)
  }

  // Place the bubble once it's rendered and its size is known (no extra render).
  useLayoutEffect(() => {
    const target = anchor.current?.getBoundingClientRect()
    const el = bubble.current
    if (!open || !target || !el) return
    const { width, height } = el.getBoundingClientRect()
    const left = Math.min(
      Math.max(EDGE, target.left + target.width / 2 - width / 2),
      window.innerWidth - width - EDGE,
    )
    const above = target.top - height - GAP
    el.style.left = `${left}px`
    el.style.top = `${above >= EDGE ? above : target.bottom + GAP}px`
    el.style.visibility = 'visible'
  }, [open])

  return (
    <span
      ref={anchor}
      className={`inline-flex ${className}`}
      onMouseEnter={() => show(DELAY)}
      onMouseLeave={hide}
      onFocus={() => show(0)}
      onBlur={hide}
      onKeyDown={(e) => e.key === 'Escape' && hide()}
    >
      {isValidElement(children)
        ? cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, {
            'aria-describedby': open ? id : undefined,
          })
        : children}
      {open &&
        createPortal(
          <div
            ref={bubble}
            id={id}
            role="tooltip"
            style={{ position: 'fixed', left: 0, top: 0, visibility: 'hidden' }}
            className="pointer-events-none z-50 max-w-xs rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-text shadow-lg"
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  )
}
