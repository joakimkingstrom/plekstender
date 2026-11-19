import { Link, NavLink, Outlet } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { SOURCES, useListens, useListenSummary } from '../api/listens'
import { ListenList } from '../components/listens'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCount, formatDate } from '../format'

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
        <NavLink to="/history/map" className={tab}>
          Map
        </NavLink>
      </nav>
      <Outlet />
    </>
  )
}

export function ListensPage() {
  const summary = useListenSummary()
  const listens = useListens()

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
          <ListenList items={items} deletable />
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
