import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-on-accent hover:bg-accent-strong',
  secondary: 'bg-surface-2 text-text hover:bg-border',
  ghost: 'text-muted hover:text-text hover:bg-surface-2',
  danger: 'text-danger hover:bg-danger/10',
}

export function Button({
  variant = 'secondary',
  busy = false,
  className = '',
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  )
}

export function Card({
  title,
  action,
  children,
  className = '',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-2xl border border-border bg-surface p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Spinner({ small = false }: { small?: boolean }) {
  const size = small ? 'size-4' : 'size-6'
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`${size} inline-block animate-spin rounded-full border-2 border-current border-r-transparent`}
    />
  )
}

export function Notice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'error' | 'warn'
  children: ReactNode
}) {
  const tones = {
    info: 'border-border bg-surface-2 text-text',
    error: 'border-danger/40 bg-danger/10 text-danger',
    warn: 'border-warn/40 bg-warn/10 text-warn',
  }
  return (
    <div role={tone === 'error' ? 'alert' : undefined} className={`rounded-lg border px-4 py-3 text-sm ${tones[tone]}`}>
      {children}
    </div>
  )
}

export function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted">
      <span className={`size-2 rounded-full ${ok ? 'bg-ok' : 'bg-danger'}`} aria-hidden />
      {label}
    </span>
  )
}

export function Avatar({ url, name }: { url?: string | null; name: string }) {
  if (url) {
    return <img src={url} alt="" className="size-8 rounded-full object-cover" referrerPolicy="no-referrer" />
  }
  return (
    <span className="flex size-8 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold">
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}

export function Badge({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'accent' | 'ok' }) {
  const tones = {
    muted: 'bg-surface-2 text-muted',
    accent: 'bg-accent/15 text-accent',
    ok: 'bg-ok/15 text-ok',
  }
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>
}

export function PageTitle({ children, subtitle }: { children: ReactNode; subtitle?: ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-bold tracking-tight">{children}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
    </div>
  )
}
