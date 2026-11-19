/**
 * A row of toggle buttons for picking one of a few options. Same height and border as the
 * site's dropdowns (Select), so they line up in toolbars.
 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  className = '',
}: {
  value: T
  options: [T, string][]
  onChange: (value: T) => void
  label: string
  /** e.g. "w-full" to stretch the buttons across a row. */
  className?: string
}) {
  return (
    <div
      className={`inline-flex rounded-lg border border-border bg-surface-2 p-0.5 ${className}`}
      role="group"
      aria-label={label}
    >
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          onClick={() => onChange(key)}
          className={`flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium ${
            key === value ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}
