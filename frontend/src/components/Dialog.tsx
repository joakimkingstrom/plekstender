import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * A modal dialog: Esc, the close button or a click outside closes it, the page behind
 * doesn't scroll, and focus moves in on open and back where it was on close.
 */
export function Dialog({
  title,
  subtitle,
  onClose,
  children,
  panelClassName = 'max-h-[90dvh] w-full max-w-2xl',
  fill = false,
}: {
  title: string
  subtitle?: ReactNode
  onClose: () => void
  children: ReactNode
  /** The panel's size (by default up to 42rem wide, and 90% of the window's height). */
  panelClassName?: string
  /** The contents fill the panel's height (and scroll themselves), rather than the panel
   * scrolling them: for something sized to the window, like choosing a cover. */
  fill?: boolean
}) {
  const id = useId()
  const panel = useRef<HTMLDivElement>(null)
  const [previous] = useState(() => document.activeElement as HTMLElement | null)
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // (Esc that something inside already handled, like closing a dropdown, stays there.)
      if (e.key === 'Escape' && !e.defaultPrevented) onCloseRef.current()
      // Keep Tab inside the dialog.
      if (e.key === 'Tab' && panel.current) {
        const focusable = panel.current.querySelectorAll<HTMLElement>(
          'button, a[href], input, [tabindex]:not([tabindex="-1"])',
        )
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last?.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first?.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [previous])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        className={`flex ${panelClassName} flex-col rounded-t-2xl border border-border bg-surface shadow-2xl sm:rounded-2xl`}
      >
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 id={`${id}-title`} className="text-base font-semibold">
              {title}
            </h2>
            {subtitle && <p className="text-xs text-muted">{subtitle}</p>}
          </div>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            aria-label="Close"
            className="flex size-8 shrink-0 items-center justify-center rounded-full text-lg text-muted hover:bg-surface-2 hover:text-text"
          >
            ×
          </button>
        </header>
        <div
          className={`${fill ? 'flex min-h-0 flex-1 flex-col' : 'overflow-y-auto'} px-5 py-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] sm:pb-4`}
        >
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
