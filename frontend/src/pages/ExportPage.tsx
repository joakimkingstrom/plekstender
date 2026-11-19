import { useMe, useServers } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { LogoPackExport, LogoPackImport } from '../components/LogoPacks'
import { LastFmImportCard, ListenBrainzImportCard } from './ListeningSettingsPage'
import { Card, Spinner } from '../components/ui'

const linkButton =
  'inline-flex items-center rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold hover:bg-border'

/** Everything you can take out of Plekstender (and bring back in). */
export function ExportPage() {
  const servers = useServers()
  const me = useMe()
  const { active } = useActiveServer()
  if (servers.isPending || me.isPending) return <Spinner />
  const withLibrary = (servers.data ?? []).filter((s) => s.music_section_keys.length > 0)
  const owner = active?.is_owner ? active : null
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">Import</h2>
        <LastFmImportCard isAdmin={Boolean(me.data?.is_admin)} />
        <ListenBrainzImportCard />
        {owner && <LogoPackImport serverId={owner.id} />}
      </div>
      <div className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">Export</h2>
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
              One file in ListenBrainz's format (JSON lines), oldest first, with each play once.
            </p>
          </div>
        </Card>
        {owner && <LogoPackExport serverId={owner.id} />}
      </div>
    </div>
  )
}
