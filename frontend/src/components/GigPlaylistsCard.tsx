import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api, errorMessage, unwrap, type Schemas } from '../api/client'
import { useMe, useSetPreferences } from '../api/hooks'
import { formatDate } from '../format'
import { Card, Notice, Spinner } from './ui'

const KEY = ['gigs', 'playlists'] as const
type Choice = Required<Schemas['GigPlaylistSettings']>

const OPTIONS: [keyof Choice, string, string][] = [
  [
    'before',
    'Before a gig',
    "For gigs you've starred, from a month before: the songs the artists have been playing lately (their recent setlists), refreshed weekly until the gig.",
  ],
  [
    'after',
    'After a gig',
    'For gigs you went to (Gigs → Been to), in the two weeks after: what was played, once the setlist is on setlist.fm.',
  ],
]

/** Playlists made for gigs (in Plex, from setlist.fm's setlists), if you want them. */
export function GigPlaylistsCard() {
  const me = useMe()
  const save = useSetPreferences()
  const qc = useQueryClient()
  const made = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/gigs/playlists')) })
  const check = useMutation({
    mutationFn: () => unwrap(api.POST('/api/gigs/playlists/check')),
    onSuccess: () => setTimeout(() => void qc.invalidateQueries({ queryKey: KEY }), 15_000),
  })
  const chosen: Choice = { before: false, after: false, ...me.data?.preferences?.gig_playlists }
  const items = made.data?.items ?? []

  return (
    <Card title="Gig playlists">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Playlists in Plex made from setlists on setlist.fm, with the songs your library has, in the order they're
          played.
        </p>
        {made.data && !made.data.setlistfm && (
          <Notice tone="warn">These need setlist.fm: an admin can add its key on Settings → Services → Gigs.</Notice>
        )}
        <fieldset className="space-y-2">
          <legend className="sr-only">Gig playlists</legend>
          {OPTIONS.map(([key, label, hint]) => (
            <label key={key} className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={chosen[key]}
                onChange={(e) => {
                  const on = e.target.checked
                  save.mutate({ gig_playlists: { ...chosen, [key]: on } }, { onSuccess: () => on && check.mutate() })
                }}
              />
              <span>
                <span className="font-medium">{label}</span>
                <span className="block text-xs text-muted">{hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        {check.isSuccess && <p className="text-xs text-muted">Looking for gigs now: playlists show up in a minute or two.</p>}
        {made.isPending ? (
          <Spinner small />
        ) : items.length > 0 ? (
          <ul className="space-y-1.5 border-t border-border pt-3">
            {items.map((p) => (
              <li key={`${p.kind}-${p.gig_date}-${p.playlist_id ?? p.name}`} className="flex items-baseline justify-between gap-3">
                {p.playlist_id ? (
                  <Link to={`/library/playlists/${p.playlist_id}`} className="min-w-0 truncate hover:underline">
                    {p.name}
                  </Link>
                ) : (
                  <span className="min-w-0 truncate text-muted">
                    {p.kind === 'before' ? 'Before' : 'After'} the gig on {formatDate(p.gig_date)}:{' '}
                    {p.songs === 0 ? 'none of its songs are in your library' : 'deleted'}
                  </span>
                )}
                {p.playlist_id && <span className="shrink-0 text-xs text-muted">{p.songs} songs</span>}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  )
}
