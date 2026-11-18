import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useHealth, useMe, useServers } from '../api/hooks'
import { SyncPanel } from '../components/library'
import { Badge, Card, Notice, PageTitle, Spinner, StatusDot } from '../components/ui'
import { ServersPage } from './ServersPage'

const UPCOMING = [
  {
    title: 'Rate',
    to: '/rate',
    text: 'Swipe through songs, albums and artists you know, and rate them in seconds.',
  },
  {
    title: 'Artwork',
    to: '/artwork',
    text: 'Swap artist photos for logos from fanart.tv, image search, uploads or custom fonts.',
  },
  {
    title: 'Playlists',
    to: '/playlists',
    text: 'Build playlists from artists, genres or moods, with similar artists and rating filters.',
  },
]

export function HomePage() {
  const { data: me } = useMe()
  const servers = useServers()
  const health = useHealth()

  if (servers.isPending) return <Spinner />
  if (servers.isError) return <Notice tone="error">{errorMessage(servers.error)}</Notice>
  if (servers.data.length === 0) return <ServersPage firstRun />

  const needsLibrary = servers.data.some((s) => s.music_section_keys.length === 0)

  return (
    <>
      <PageTitle subtitle="Here's what Plekstender is set up to work with.">
        Hi {me?.username}
      </PageTitle>

      {needsLibrary && (
        <div className="mb-4">
          <Notice tone="warn">
            Pick a music library for each server in <Link className="underline" to="/settings">Settings</Link>.
          </Notice>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {servers.data
          .filter((s) => s.music_section_keys.length > 0)
          .map((s) => (
            <Card
              key={s.id}
              title={servers.data.length > 1 ? `Library · ${s.name}` : 'Library'}
              action={<Link to="/library" className="text-sm text-accent">Browse</Link>}
              className="md:col-span-2"
            >
              <SyncPanel serverId={s.id} />
            </Card>
          ))}
        <Card title="Servers" action={<Link to="/settings" className="text-sm text-accent">Manage</Link>}>
          <ul className="space-y-2">
            {servers.data.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="font-medium">{s.name}</span>
                <Badge>{s.music_section_keys.length} music {s.music_section_keys.length === 1 ? 'library' : 'libraries'}</Badge>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Status">
          {health.data ? (
            <div className="flex flex-col gap-2">
              <StatusDot ok={health.data.database} label="Database" />
              <StatusDot
                ok={health.data.worker.healthy}
                label={health.data.worker.healthy ? 'Background worker' : 'Background worker not running'}
              />
              <span className="text-xs text-muted">Plekstender {health.data.version}</span>
            </div>
          ) : (
            <Spinner small />
          )}
        </Card>
      </div>

      <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-wider text-muted">Coming up</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {UPCOMING.map((item) => (
          <Link key={item.to} to={item.to} className="rounded-2xl border border-dashed border-border p-5 hover:border-accent/50">
            <div className="mb-1 font-semibold">{item.title}</div>
            <p className="text-sm text-muted">{item.text}</p>
          </Link>
        ))}
      </div>
    </>
  )
}

export function ComingSoonPage({ title, text }: { title: string; text: string }) {
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <Card>
        <p className="text-sm text-muted">{text}</p>
      </Card>
    </>
  )
}

export function NotFoundPage() {
  return (
    <>
      <PageTitle>Not found</PageTitle>
      <Link to="/" className="text-accent">Back home</Link>
    </>
  )
}
