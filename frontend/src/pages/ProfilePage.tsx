import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api, errorMessage, unwrap } from '../api/client'
import { useMe, useSetPreferences } from '../api/hooks'
import { AchievementList } from '../components/Achievements'
import { GigPlaylistsCard } from '../components/GigPlaylistsCard'
import { Segmented } from '../components/Segmented'
import { Avatar, Badge, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCount, formatDate } from '../format'
import { AppearancePage } from './SettingsPage'

function useProfile() {
  return useQuery({ queryKey: ['me', 'profile'], queryFn: () => unwrap(api.GET('/api/auth/me/profile')) })
}

function Toggle({ label, checked, onChange, children }: { label: string; checked: boolean; onChange: (on: boolean) => void; children?: string }) {
  return (
    <label className="flex items-start gap-3 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5" />
      <span>
        <span className="font-medium">{label}</span>
        {children && <span className="block text-muted">{children}</span>}
      </span>
    </label>
  )
}

/** /profile (from the avatar): who you are here, a few numbers, and your preferences. */
export function ProfilePage() {
  const me = useMe()
  const profile = useProfile()
  const save = useSetPreferences()
  const qc = useQueryClient()
  if (profile.isPending || me.isPending) return <Spinner />
  if (profile.isError) return <Notice tone="error">{errorMessage(profile.error)}</Notice>
  const p = profile.data
  const prefs = me.data?.preferences
  const numbers: [string, number, string][] = [
    ['Scrobbles', p.scrobbles, '/history/scrobbles'],
    ['Ratings', p.ratings, '/history/ratings'],
    ['Gigs been to', p.gigs, '/gigs/been-to'],
  ]
  return (
    <>
      <div className="mb-6 flex items-center gap-4">
        <span className="[&>*]:size-16 [&>*]:text-2xl">
          <Avatar url={p.thumb_url} name={p.username} />
        </span>
        <div className="min-w-0">
          <PageTitle subtitle={[p.email, `Here since ${formatDate(p.joined_at)}`].filter(Boolean).join(' · ')}>
            {p.username}
          </PageTitle>
        </div>
        {p.is_admin && <Badge tone="accent">Admin</Badge>}
      </div>

      <div className="mb-6 grid grid-cols-3 gap-3">
        {numbers.map(([label, value, to]) => (
          <Link key={label} to={to} className="rounded-xl border border-border bg-surface p-4 hover:border-accent/50">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
            <p className="font-display text-2xl font-bold tabular-nums sm:text-3xl">{formatCount(value)}</p>
          </Link>
        ))}
      </div>
      {p.first_listen_at && <p className="-mt-3 mb-6 text-xs text-muted">Your listening history goes back to {formatDate(p.first_listen_at)}.</p>}

      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <Card title="Servers" action={<Link to="/settings" className="text-sm text-accent hover:underline">Manage</Link>}>
          <ul className="space-y-1.5 text-sm">
            {p.servers.map((s) => (
              <li key={s.name} className="flex items-center justify-between gap-2">
                <span className="font-medium">{s.name}</span>
                <Badge>{s.is_owner ? 'Yours' : 'Shared with you'}</Badge>
              </li>
            ))}
            {p.servers.length === 0 && <li className="text-muted">None yet.</li>}
          </ul>
        </Card>
        <Card title="Theme">
          <div className="space-y-3 text-sm">
            <Segmented
              label="Theme"
              value={prefs?.theme ?? 'browser'}
              options={[
                ['dark', 'Dark'],
                ['light', 'Light'],
                ['browser', 'Per device'],
              ]}
              onChange={(v) => save.mutate({ theme: v === 'browser' ? null : v })}
            />
            <p className="text-muted">
              Dark or light saves the choice to your account, so every device follows it. Per device: the ☀ / ☾ button
              in the header sets it for this browser only.
            </p>
          </div>
        </Card>
        <GigPlaylistsCard />
      </div>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted">Appearance</h2>
      <div className="mb-4">
        <AppearancePage />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Notifications">
          <div className="space-y-3">
            <p className="text-sm text-muted">
              The bell in the header tells you when one of your connections stops working: a server, a scrobble service,
              a calendar link.
            </p>
            {p.is_admin && (
              <Toggle
                label="This Plekstender's services too"
                checked={prefs?.instance_notifications ?? true}
                onChange={(instance_notifications) => save.mutate({ instance_notifications })}
              >
                Service keys and their limits (JamBase, Ticketmaster, Data Thistle), and image search. Other admins get
                them either way.
              </Toggle>
            )}
          </div>
        </Card>
        <Card title="Lyrics icons">
          <Toggle
            label="Show which songs have lyrics"
            checked={prefs?.lyrics_icons ?? false}
            onChange={(lyrics_icons) =>
              save.mutate(
                { lyrics_icons },
                // Artist and album pages already open fetch them again, with the lyrics.
                { onSuccess: () => void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'servers' && q.queryKey[2] === 'library' }) },
              )
            }
          >
            A small icon on songs (plain, synced or word-synced lyrics, or none) and on albums (all, some or none of
            their songs), from what the music files have. Needs the music folder read on Settings → Music files.
          </Toggle>
        </Card>
        <Card title="Achievements">
          <Toggle label="Achievements" checked={prefs?.achievements ?? true} onChange={(achievements) => save.mutate({ achievements })}>
            Small, silly rewards for milestones in your library, ratings, gigs and games, with a pop-up when one
            unlocks. Off: no pop-ups, and the list is hidden.
          </Toggle>
        </Card>
      </div>
      {prefs?.achievements !== false && (
        <>
          <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-wider text-muted">Achievements</h2>
          <AchievementList />
        </>
      )}
      {save.isError && <div className="mt-4"><Notice tone="error">{errorMessage(save.error)}</Notice></div>}
    </>
  )
}
