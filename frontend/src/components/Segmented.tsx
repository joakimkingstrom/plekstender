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
  size = 'md',
  prefix,
}: {
  value: T
  options: [T, string][]
  onChange: (value: T) => void
  label: string
  /** e.g. "w-full" to stretch the buttons across a row. */
  className?: string
  /** sm: compact, to sit beside a small dropdown in a narrow column. */
  size?: 'sm' | 'md'
  /** A word before the buttons, inside the same box ("Show"), saying what they choose (left
   * out on narrow windows, where the room is the buttons'). */
  prefix?: string
}) {
  return (
    <div
      className={`inline-flex rounded-lg border border-border bg-surface-2 p-0.5 ${className}`}
      role="group"
      aria-label={label}
    >
      {prefix && (
        <span className={`flex items-center pl-2.5 pr-1.5 text-muted max-sm:hidden ${size === 'sm' ? 'text-xs' : 'text-sm'}`} aria-hidden>
          {prefix}
        </span>
      )}
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          onClick={() => onChange(key)}
          className={`flex-1 whitespace-nowrap rounded-md font-medium ${size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1.5 text-sm'} ${
            key === value ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}
