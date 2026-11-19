import { Link } from 'react-router'
import { useServers } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { LogoPacks } from '../components/LogoPacks'
import { Card, Spinner } from '../components/ui'

const linkButton =
  'inline-flex items-center rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold hover:bg-border'

/** Everything you can take out of Plekstender (and bring back in). */
export function ExportPage() {
  const servers = useServers()
  const { active } = useActiveServer()
  if (servers.isPending) return <Spinner />
  const withLibrary = (servers.data ?? []).filter((s) => s.music_section_keys.length > 0)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card title="Library, with your ratings and plays">
          <div className="space-y-3 text-sm">
            <p className="text-muted">
              Every song with its album and artist, your rating and play count. The JSON file also has artists and
              albums with their genres and your ratings.
            </p>
            {withLibrary.length === 0 ? (
              <p className="text-muted">Connect a server with a music library first.</p>
            ) : (
              <ul className="space-y-3">
                {withLibrary.map((s) => (
                  <li key={s.id} className="space-y-1.5">
                    {withLibrary.length > 1 && <p className="font-medium">{s.name}</p>}
                    <div className="flex flex-wrap gap-2">
                      <a className={linkButton} href={`/api/servers/${s.id}/export/tracks.csv`} download>
                        Songs (CSV)
                      </a>
                      <a className={linkButton} href={`/api/servers/${s.id}/export/library.json`} download>
                        Full library (JSON)
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
        <Card title="Listening history">
          <div className="space-y-3 text-sm">
            <a className={linkButton} href="/api/listens/export.jsonl" download>
              Download all listens
            </a>
            <p className="text-xs text-muted">
              One file in ListenBrainz's format (JSON lines), oldest first, with each play once. To import history from
              Last.fm or ListenBrainz, see{' '}
              <Link to="/settings/listening" className="text-accent underline">
                Listening
              </Link>
              .
            </p>
          </div>
        </Card>
      </div>
      <div className="space-y-4">
        {active?.is_owner ? (
          <LogoPacks serverId={active.id} />
        ) : (
          <Card title="Artist logo packs">
            <p className="text-sm text-muted">
              Logo packs export and import the artist images on a server you own.
            </p>
          </Card>
        )}
      </div>
    </div>
  )
}
