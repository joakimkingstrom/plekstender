import { useEffect, useId, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from 'react'
import { Button } from './ui'

/** A range slider whose track is filled up to the thumb. */
export function Slider({
  label,
  value,
  min,
  max,
  step,
  disabled,
  format = (v) => v.toFixed(1),
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  disabled?: boolean
  format?: (value: number) => string
  onChange: (value: number) => void
}) {
  const fill = `${((value - min) / (max - min)) * 100}%`
  return (
    <label className={`block ${disabled ? 'opacity-50' : ''}`}>
      <span className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted tabular-nums">{format(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
        style={{ ['--fill' as string]: fill }}
      />
    </label>
  )
}

/** A whole-number field with − / + buttons instead of the browser's spinners. */
export function Stepper({
  value,
  min,
  max,
  onChange,
  label,
  invalid = false,
}: {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  label: string
  invalid?: boolean
}) {
  const valid = Number.isFinite(value)
  const step = (delta: number) => onChange(Math.min(max, Math.max(min, (valid ? value : min) + delta)))
  const button = 'flex w-8 items-center justify-center text-muted hover:text-text disabled:opacity-30'
  return (
    <span className={`inline-flex h-9 overflow-hidden rounded-lg border bg-surface-2 ${invalid ? 'border-danger' : 'border-border'}`}>
      <button type="button" className={button} onClick={() => step(-1)} disabled={valid && value <= min} aria-label={`Decrease ${label}`}>
        −
      </button>
      <input
        type="number"
        inputMode="numeric"
        min={min}
        max={max}
        step={1}
        value={valid ? value : ''}
        onChange={(e) => onChange(e.target.valueAsNumber)}
        aria-label={label}
        aria-invalid={invalid}
        className="w-14 bg-transparent text-center text-sm tabular-nums focus:outline-none"
      />
      <button type="button" className={button} onClick={() => step(1)} disabled={valid && value >= max} aria-label={`Increase ${label}`}>
        +
      </button>
    </span>
  )
}

/** Choose a file with a button or by dropping it on the area. */
export function FilePicker({
  accept,
  onFile,
  label,
  hint,
}: {
  accept: string
  onFile: (file: File) => void
  label: string
  hint?: ReactNode
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [name, setName] = useState<string | null>(null)
  const take = (file: File | undefined) => {
    if (!file) return
    setName(file.name)
    onFile(file)
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    take(e.dataTransfer.files[0])
  }
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={`flex flex-wrap items-center gap-3 rounded-xl border-2 border-dashed p-3 transition-colors ${
        over ? 'border-accent bg-accent/5' : 'border-border'
      }`}
    >
      <input
        ref={input}
        type="file"
        accept={accept}
        aria-label={label}
        className="sr-only"
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          take(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      <Button onClick={() => input.current?.click()}>{label}</Button>
      <span className="min-w-0 flex-1 truncate text-xs text-muted">{name ?? hint ?? 'or drop a file here'}</span>
    </div>
  )
}

const SWATCHES = [
  '#ffffff', '#d4d4d4', '#8a8a8a', '#3a3a3a', '#000000',
  '#f2ad45', '#d9821a', '#e5484d', '#e93d82', '#8e4ec6',
  '#3e63dd', '#0090ff', '#12a594', '#30a46c', '#ffc53d',
]  // prettier-ignore

/** A colour button that opens preset swatches and a hex field (no browser colour dialog). */
export function ColorPicker({
  value,
  onChange,
  label,
}: {
  value: string
  onChange: (hex: string) => void
  label: string
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(value)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const commit = (hex: string) => {
    const clean = hex.trim().startsWith('#') ? hex.trim() : `#${hex.trim()}`
    if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(clean)) onChange(clean.toLowerCase())
  }

  return (
    <div ref={root} className="relative inline-block">
      <button
        type="button"
        aria-label={`${label}: ${value}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setText(value)
          setOpen(!open)
        }}
        className="flex h-9 items-center gap-2 rounded-lg border border-border bg-surface-2 px-2 text-xs tabular-nums hover:border-muted"
      >
        <span className="size-5 rounded border border-border" style={{ background: value }} />
        {value}
      </button>
      {open && (
        <div
          id={id}
          role="dialog"
          aria-label={label}
          onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
          className="absolute left-0 z-40 mt-1 w-52 space-y-2 rounded-xl border border-border bg-surface p-3 shadow-xl"
        >
          <div className="grid grid-cols-5 gap-1.5">
            {SWATCHES.map((hex) => (
              <button
                key={hex}
                type="button"
                aria-label={hex}
                aria-pressed={hex === value.toLowerCase()}
                onClick={() => {
                  onChange(hex)
                  setOpen(false)
                }}
                className={`aspect-square rounded-md border ${hex === value.toLowerCase() ? 'ring-2 ring-accent ring-offset-1 ring-offset-surface' : 'border-border'}`}
                style={{ background: hex }}
              />
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs">
            Hex
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onBlur={() => commit(text)}
              onKeyDown={(e) => e.key === 'Enter' && (commit(text), setOpen(false))}
              maxLength={7}
              spellCheck={false}
              className="min-w-0 flex-1 rounded-md border border-border bg-surface-2 px-2 py-1 font-mono"
            />
          </label>
        </div>
      )}
    </div>
  )
}
