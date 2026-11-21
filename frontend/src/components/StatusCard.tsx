import { Link } from 'react-router'
import { useConnections, useHealth, type ConnectionStatus } from '../api/hooks'
import { Card, Spinner } from './ui'

type State = ConnectionStatus['state']
type Item = Pick<ConnectionStatus, 'name' | 'state'> & Partial<ConnectionStatus>

const DOT: Record<State, string> = { ok: 'bg-ok', warning: 'bg-warn', error: 'bg-danger', off: 'bg-border' }
const TEXT: Record<State, string> = { ok: 'text-muted', warning: 'text-warn', error: 'text-danger', off: 'text-muted' }
const BAR: Record<State, string> = { ok: 'bg-accent', warning: 'bg-warn', error: 'bg-danger', off: 'bg-border' }
const WORD: Record<State, string> = { ok: 'working', warning: 'needs a look', error: 'not working', off: 'not set up' }

/** Home → Status: a summary up top, then Plekstender itself, your connections and the
 * services set up here, each a small tile (with a meter for services with a request limit). */
export function StatusCard() {
  const health = useHealth()
  const connections = useConnections()
  if (!health.data)
    return (
      <Card title="Status">
        <Spinner small />
      </Card>
    )
  const system: Item[] = [
    { name: 'Database', state: health.data.database ? 'ok' : 'error' },
    {
      name: 'Background worker',
      state: health.data.worker.healthy ? 'ok' : 'error',
      detail: health.data.worker.healthy ? null : 'Not running',
    },
  ]
  const all = connections.data ?? []
  const yours = all.filter((c) => c.group === 'yours')
  const services = all.filter((c) => c.group === 'instance' && c.state !== 'off')
  const off = all.filter((c) => c.group === 'instance' && c.state === 'off')
  const shown = [...system, ...yours, ...services]
  const errors = shown.filter((c) => c.state === 'error').length
  const warnings = shown.filter((c) => c.state === 'warning').length
  const summary: [State, string] = errors
    ? ['error', `${errors} not working`]
    : warnings
      ? ['warning', `${warnings} ${warnings === 1 ? 'needs' : 'need'} a look`]
      : ['ok', 'All working']
  const pill = { ok: 'bg-ok/15 text-ok', warning: 'bg-warn/15 text-warn', error: 'bg-danger/15 text-danger', off: '' }[summary[0]]

  return (
    <Card
      title="Status"
      action={
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${pill}`}>
          <span className={`size-1.5 rounded-full ${DOT[summary[0]]}`} aria-hidden />
          {summary[1]}
        </span>
      }
    >
      <div className="space-y-4">
        <Group title="Plekstender" items={system} />
        <Group title="Yours" items={yours} />
        <Group title="Services on this Plekstender" items={services} />
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-t border-border pt-3 text-xs text-muted">
          <span>{off.length > 0 && `Not set up: ${off.map((c) => c.name).join(', ')}`}</span>
          <span className="tabular-nums">Plekstender {health.data.version}</span>
        </div>
      </div>
    </Card>
  )
}

function Group({ title, items }: { title: string; items: Item[] }) {
  if (items.length === 0) return null
  return (
    <section aria-label={title}>
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h3>
      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {items.map((c, i) => (
          <Tile key={`${c.name}-${c.subtitle ?? ''}-${i}`} item={c} />
        ))}
      </ul>
    </section>
  )
}

function Tile({ item: c }: { item: Item }) {
  return (
    <li className="min-w-0 rounded-lg border border-border bg-surface-2/40 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={`size-2 shrink-0 rounded-full ${DOT[c.state]}`} aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={`${c.name}: ${WORD[c.state]}`}>
          <span>{c.name}</span>
          <span className="sr-only">: {WORD[c.state]}</span>
        </span>
        {c.link && c.state !== 'ok' && (
          <Link to={c.link} className="shrink-0 text-xs text-accent hover:underline">
            Fix
          </Link>
        )}
      </div>
      {c.subtitle && (
        <p className="ml-4 truncate text-xs text-muted" title={c.subtitle}>
          {c.subtitle}
        </p>
      )}
      {c.detail && <p className={`ml-4 text-xs ${TEXT[c.state]}`}>{c.detail}</p>}
      {c.usage != null && (
        <div className="ml-4 mt-1.5 h-1 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <div className={`h-full rounded-full ${BAR[c.state]}`} style={{ width: `${Math.max(2, c.usage * 100)}%` }} />
        </div>
      )}
    </li>
  )
}
