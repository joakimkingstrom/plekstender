import { useRef, type ReactNode } from 'react'

/** Results in one row that scrolls sideways (drag, wheel or the arrows at either end). */
export function ResultStrip({ label, children }: { label: string; children: ReactNode }) {
  const list = useRef<HTMLUListElement>(null)
  const scroll = (direction: 1 | -1) =>
    list.current?.scrollBy({
      left: direction * list.current.clientWidth * 0.8,
      behavior: 'smooth',
    })
  return (
    <div className="relative min-h-0 flex-1">
      <ul
        ref={list}
        aria-label={label}
        onWheel={(e) => {
          // A mouse wheel scrolls the row sideways (a trackpad already does).
          if (list.current && Math.abs(e.deltaY) > Math.abs(e.deltaX)) list.current.scrollLeft += e.deltaY
        }}
        className="flex h-full gap-3 overflow-x-auto overflow-y-hidden px-1 pb-2"
      >
        {children}
      </ul>
      <StripArrow side="left" label={`Scroll ${label.toLowerCase()} left`} onClick={() => scroll(-1)} />
      <StripArrow side="right" label={`Scroll ${label.toLowerCase()} right`} onClick={() => scroll(1)} />
    </div>
  )
}

function StripArrow({ side, label, onClick }: { side: 'left' | 'right'; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={`absolute top-[38%] hidden size-9 items-center justify-center rounded-full border border-border bg-surface-2/95 text-text shadow hover:bg-border sm:flex ${side === 'left' ? '-left-2' : '-right-2'}`}
    >
      <svg
        viewBox="0 0 16 16"
        className="size-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d={side === 'left' ? 'M10 3L5 8l5 5' : 'M6 3l5 5-5 5'} />
      </svg>
    </button>
  )
}
