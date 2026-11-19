import { rangeOptions } from '../ranges'

/** A row of period chips (scrolls sideways on small screens). */
export function RangePicker({
  range,
  firstListen,
  onChange,
}: {
  range: string
  firstListen: string | null | undefined
  onChange: (range: string) => void
}) {
  return (
    <div className="no-scrollbar -mx-4 overflow-x-auto px-4">
      <div className="flex w-max gap-1.5" role="group" aria-label="Period">
        {rangeOptions(firstListen).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={key === range}
            onClick={() => onChange(key)}
            className={`rounded-full px-3 py-1.5 text-sm font-medium ${
              key === range ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}
