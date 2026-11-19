/** A row of toggle buttons for picking one of a few options. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: [T, string][]
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="inline-flex rounded-lg bg-surface-2 p-0.5" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          onClick={() => onChange(key)}
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${
            key === value ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}
