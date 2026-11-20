import { Navigate } from 'react-router'
import { useState } from 'react'
import { errorMessage, type Schemas } from '../api/client'
import {
  useAdminAccess,
  useAdminServers,
  useAdminUsers,
  useApproveServer,
  useMe,
  useRemoveUser,
  useUpdateAdminAccess,
  useUpdateUser,
} from '../api/hooks'
import { Avatar, Badge, Button, Card, Notice, Spinner } from '../components/ui'
import { useAchievementSettings, useSetAchievementSettings } from '../api/achievements'

type Mode = Schemas['AccessMode']

const MODES: { value: Mode; label: string; text: string }[] = [
  {
    value: 'servers',
    label: 'Approved servers',
    text: 'Anyone with access to a server you approve below (e.g. friends you share your Plex server with).',
  },
  { value: 'allowlist', label: 'Allowlist', text: 'Only the Plex usernames or emails you list.' },
  { value: 'open', label: 'Open', text: 'Any Plex account. Only use this if you know who can reach this site.' },
]

function AccessCard() {
  const access = useAdminAccess()
  if (access.isPending) return <Card title="Who can sign in"><Spinner small /></Card>
  if (access.isError) return <Notice tone="error">{errorMessage(access.error)}</Notice>
  return <AccessForm initial={access.data} />
}

function AccessForm({ initial }: { initial: Schemas['AccessSettingsOut'] }) {
  const update = useUpdateAdminAccess()
  const [mode, setMode] = useState<Mode>(initial.mode)
  const [allowlist, setAllowlist] = useState(initial.allowlist.join('\n'))

  const save = () =>
    update.mutate(
      { mode, allowlist: allowlist.split(/[\n,]/).map((s) => s.trim()).filter(Boolean) },
      { onSuccess: (data) => setAllowlist(data.allowlist.join('\n')) },
    )

  return (
    <Card title="Who can sign in">
      <div className="space-y-3">
        {MODES.map((m) => (
          <label key={m.value} className="flex gap-3 rounded-lg border border-border p-3 has-checked:border-accent">
            <input
              type="radio"
              name="mode"
              className="mt-1 accent-accent"
              checked={mode === m.value}
              onChange={() => setMode(m.value)}
            />
            <span>
              <span className="block text-sm font-medium">{m.label}</span>
              <span className="block text-xs text-muted">{m.text}</span>
            </span>
          </label>
        ))}
        {mode === 'allowlist' && (
          <label className="block text-sm">
            <span className="mb-1 block text-muted">Plex usernames or emails, one per line</span>
            <textarea
              value={allowlist}
              onChange={(e) => setAllowlist(e.target.value)}
              rows={4}
              className="w-full rounded-lg border border-border bg-surface-2 p-2 text-sm"
            />
          </label>
        )}
        <p className="text-xs text-muted">Admins can always sign in.</p>
        {update.isError && <Notice tone="error">{errorMessage(update.error)}</Notice>}
        {update.isSuccess && !update.isPending && <p className="text-xs text-ok">Saved.</p>}
        <Button variant="primary" busy={update.isPending} onClick={save}>
          Save
        </Button>
      </div>
    </Card>
  )
}

function ServersCard() {
  const servers = useAdminServers()
  const approve = useApproveServer()
  return (
    <Card title="Servers">
      {servers.isPending && <Spinner small />}
      {servers.isError && <Notice tone="error">{errorMessage(servers.error)}</Notice>}
      {servers.data?.length === 0 && <p className="text-sm text-muted">No servers connected yet.</p>}
      <ul className="divide-y divide-border">
        {servers.data?.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-3 py-3 text-sm">
            <span>
              <span className="font-medium">{s.name}</span>
              <span className="ml-2 text-xs text-muted">
                {s.user_count} {s.user_count === 1 ? 'user' : 'users'}
              </span>
            </span>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-accent"
                checked={s.approved}
                disabled={approve.isPending}
                onChange={(e) => approve.mutate({ id: s.id, approved: e.target.checked })}
              />
              Approved
            </label>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function UsersCard({ selfId }: { selfId: number }) {
  const users = useAdminUsers()
  const update = useUpdateUser()
  const remove = useRemoveUser()
  return (
    <Card title="Users" className="lg:col-span-2">
      {users.isPending && <Spinner small />}
      {users.isError && <Notice tone="error">{errorMessage(users.error)}</Notice>}
      {(update.isError || remove.isError) && (
        <Notice tone="error">{errorMessage(update.error ?? remove.error)}</Notice>
      )}
      <ul className="divide-y divide-border">
        {users.data?.map((u) => (
          <li key={u.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
            <Avatar url={u.thumb_url} name={u.username} />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 font-medium">
                {u.username}
                {u.is_admin && <Badge tone="accent">Admin</Badge>}
              </span>
              <span className="block truncate text-xs text-muted">
                {u.server_count} {u.server_count === 1 ? 'server' : 'servers'}
                {u.last_login_at && ` · last signed in ${new Date(u.last_login_at).toLocaleDateString()}`}
              </span>
            </span>
            {u.id !== selfId && (
              <span className="flex gap-1">
                <Button variant="ghost" onClick={() => update.mutate({ id: u.id, isAdmin: !u.is_admin })}>
                  {u.is_admin ? 'Remove admin' : 'Make admin'}
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    if (window.confirm(`Remove ${u.username} and all their Plekstender data?`)) remove.mutate(u.id)
                  }}
                >
                  Remove
                </Button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** Admin settings for this Plekstender (shown under Settings → Servers, for admins). */
export function AdminSettings() {
  const { data: me, isPending } = useMe()
  if (isPending || !me?.is_admin) return null
  return (
    <section className="space-y-4" aria-labelledby="admin-heading">
      <p id="admin-heading" className="text-sm text-muted">
        Who can sign in, which servers they can use, and users. For admins only.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <AccessCard />
        <ServersCard />
        <UsersCard selfId={me.id} />
        <AchievementsCard />
      </div>
    </section>
  )
}

/** Settings → Users & access (admins). */
export function UsersPage() {
  const { data: me, isPending } = useMe()
  if (isPending) return null
  if (!me?.is_admin) return <Navigate to="/settings" replace />
  return <AdminSettings />
}

/** Achievements on or off for everyone (each person can also turn them off on their profile). */
function AchievementsCard() {
  const settings = useAchievementSettings(true)
  const set = useSetAchievementSettings()
  const on = set.isPending ? set.variables : (settings.data?.enabled ?? true)
  return (
    <Card title="Achievements">
      <label className="flex items-start gap-3 text-sm">
        <input type="checkbox" checked={on} disabled={settings.isPending} onChange={(e) => set.mutate(e.target.checked)} className="mt-0.5" />
        <span>
          <span className="font-medium">On for everyone</span>
          <span className="block text-muted">
            Small rewards for milestones, with a pop-up when one unlocks. Off: no pop-ups and no list, for anyone. Each
            person can also turn them off for themselves on their profile.
          </span>
        </span>
      </label>
    </Card>
  )
}
