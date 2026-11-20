/** A small ticket badge on an artist's picture: they have an upcoming gig. */
export function GigMarker({ className = '' }: { className?: string }) {
  return (
    <span
      role="img"
      aria-label="Upcoming gig"
      title="Upcoming gig"
      className={`pointer-events-auto absolute flex items-center justify-center rounded-full bg-accent text-on-accent shadow-md ring-2 ring-bg ${className}`}
    >
      <svg viewBox="0 0 24 24" className="size-[58%]" aria-hidden>
        <path
          fill="currentColor"
          d="M4 6.5A1.5 1.5 0 0 1 5.5 5h13A1.5 1.5 0 0 1 20 6.5V9a2.5 2.5 0 0 0 0 5v2.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 16.5V14a2.5 2.5 0 0 0 0-5zm9.5 1.25v1.5h1.5v-1.5zm0 3.5v1.5h1.5v-1.5zm0 3.5v1.5h1.5v-1.5z"
        />
      </svg>
    </span>
  )
}
