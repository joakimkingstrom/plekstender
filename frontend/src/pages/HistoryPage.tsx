import { Link, NavLink, Outlet } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useDeleteListen, useListens, useListenSummary } from '../api/listens'
import { Badge, Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCount, formatDate, formatDay, formatTime } from '../format'

const SOURCES: Record<Schemas['ListenOut']['source'], string> = {
  lastfm: 'Last.fm',
  plex: 'Plex',
  listenbrainz: 'ListenBrainz',
}

function Summary({ summary }: { summary: Schemas['ListenSummary'] }) {
  const stats: [string, string][] = [
    ['Listens', formatCount(summary.total)],
    ['Since', summary.first_at ? formatDate(summary.first_at) : '–'],
    ...Object.entries(summary.by_source).map(
      ([source, n]): [string, string] => [`From ${SOURCES[source as keyof typeof SOURCES] ?? source}`, formatCount(n)],
    ),
  ]
  return (
    <dl className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
      {stats.map(([label, value]) => (
        <div key={label} className="rounded-2xl border border-border bg-surface px-4 py-3">
          <dt className="text-xs text-muted">{label}</dt>
          <dd className="font-display text-xl font-bold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function groupByDay(items: Schemas['ListenOut'][]) {
  const days: { day: string; items: Schemas['ListenOut'][] }[] = []
  for (const item of items) {
    const day = formatDay(item.listened_at)
    if (days.at(-1)?.day !== day) days.push({ day, items: [] })
    days.at(-1)!.items.push(item)
  }
  return days
}

/** History shell: stats overview and the list of listens. */
export function HistoryLayout() {
  const tab = ({ isActive }: { isActive: boolean }) =>
    `border-b-2 px-1 pb-2 text-sm font-medium ${
      isActive ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'
    }`
  return (
    <>
      <PageTitle subtitle="What you've listened to. Only you can see this.">History</PageTitle>
      <nav className="mb-6 flex gap-6 border-b border-border" aria-label="History">
        <NavLink to="/history" end className={tab}>
          Overview
        </NavLink>
        <NavLink to="/history/listens" className={tab}>
          Listens
        </NavLink>
      </nav>
      <Outlet />
    </>
  )
}

export function ListensPage() {
  const summary = useListenSummary()
  const listens = useListens()
  const remove = useDeleteListen()

  if (summary.isPending || listens.isPending) return <Spinner />
  if (summary.isError) return <Notice tone="error">{errorMessage(summary.error)}</Notice>
  if (listens.isError) return <Notice tone="error">{errorMessage(listens.error)}</Notice>

  const items = listens.data.pages.flatMap((p) => p.items)
  return (
    <>
      {items.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            No listens yet. Plays from your Plex servers are added every few minutes once a
            library has synced, and you can import your Last.fm history on{' '}
            <Link to="/settings/listening" className="text-accent underline">
              Settings → Listening
            </Link>
            .
          </p>
        </Card>
      ) : (
        <>
          <Summary summary={summary.data} />
          {remove.isError && <Notice tone="error">{errorMessage(remove.error)}</Notice>}
          <div className="space-y-6">
            {groupByDay(items).map(({ day, items: dayItems }) => (
              <section key={day}>
                <h2 className="mb-2 text-sm font-semibold text-muted">{day}</h2>
                <ol className="divide-y divide-border rounded-2xl border border-border bg-surface">
                  {dayItems.map((listen) => (
                    <li key={listen.id} className="group flex items-center gap-3 px-4 py-2.5">
                      <time
                        dateTime={listen.listened_at}
                        className="w-12 shrink-0 text-xs text-muted tabular-nums"
                      >
                        {formatTime(listen.listened_at)}
                      </time>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{listen.track_name}</p>
                        <p className="truncate text-xs text-muted">
                          {listen.artist_name}
                          {listen.album_name && ` · ${listen.album_name}`}
                        </p>
                      </div>
                      {listen.duplicate && <Badge>Duplicate</Badge>}
                      <Badge>{SOURCES[listen.source]}</Badge>
                      <button
                        type="button"
                        onClick={() => remove.mutate(listen.id)}
                        disabled={remove.isPending}
                        className="rounded px-1.5 text-muted opacity-60 hover:bg-surface-2 hover:text-danger group-hover:opacity-100"
                        aria-label={`Delete listen: ${listen.track_name} at ${formatTime(listen.listened_at)}`}
                        title="Delete this listen"
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </div>
          {listens.hasNextPage && (
            <div className="mt-6 flex justify-center">
              <Button busy={listens.isFetchingNextPage} onClick={() => void listens.fetchNextPage()}>
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </>
  )
}
