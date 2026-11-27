import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage, unwrap, type Schemas } from '../api/client'
import { Card, Notice, Spinner } from './ui'

type CoverSources = Schemas['CoverSourcesSettings']

const KEY = ['admin', 'cover-sources'] as const

const SOURCES: { key: keyof CoverSources; name: string; about: string }[] = [
  { key: 'deezer', name: 'Deezer', about: 'Covers up to 1000 × 1000, found by album and artist.' },
  { key: 'itunes', name: 'iTunes', about: 'Covers up to 3000 × 3000 (often the biggest), found by album and artist.' },
  {
    key: 'coverartarchive',
    name: 'Cover Art Archive',
    about: 'MusicBrainz’s covers, scanned by its editors: by the album’s MusicBrainz release.',
  },
]

/** Settings → Services → Artwork (admins): the album cover sources that need no key, on or off. */
export function CoverSourcesCard() {
  const qc = useQueryClient()
  const current = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/admin/cover-sources')) })
  const save = useMutation({
    mutationFn: (body: CoverSources) => unwrap(api.PUT('/api/admin/cover-sources', { body })),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data)
      // The album covers page asks which sources it may offer.
      void qc.invalidateQueries({ predicate: (q) => q.queryKey.includes('sources') })
    },
  })
  const shown = save.isPending ? save.variables : current.data
  return (
    <Card title="Album cover sources">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          These need no key, so they're on unless turned off here. A source that's off isn't offered on Library → Artwork →
          Album covers, and best matches don't look there.
        </p>
        {current.isPending ? (
          <Spinner small />
        ) : current.isError ? (
          <Notice tone="error">{errorMessage(current.error)}</Notice>
        ) : (
          <div className="space-y-2">
            {SOURCES.map((s) => (
              <label key={s.key} className="flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={shown?.[s.key] ?? true}
                  disabled={save.isPending}
                  onChange={(e) => save.mutate({ ...current.data, [s.key]: e.target.checked })}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium">{s.name}</span>
                  <span className="block text-muted">{s.about}</span>
                </span>
              </label>
            ))}
          </div>
        )}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
