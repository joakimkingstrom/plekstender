import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router'
import { useMe, useServers } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { GigExportCard, GigImportCard } from '../components/GigTransfer'
import { LogoPackExport, LogoPackImport } from '../components/LogoPacks'
import { Card, Spinner } from '../components/ui'
import { EXPORT_PARTS, type ExportPart } from '../settingsSections'
import { LastFmImportCard, ListenBrainzImportCard } from './ListeningSettingsPage'

const linkButton =
  'inline-flex items-center rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold hover:bg-border'

/**
 * Settings → Export & import → one part (listening history, library, gigs, artist logos):
 * what you can bring in, then what you can take out. `part` is for tests; the page
 * otherwise reads it from the address.
 */
export function ExportPage({ part: given }: { part?: ExportPart } = {}) {
  const params = useParams()
  const me = useMe()
  const { active, servers } = useActiveServer()
  const owner = active?.is_owner ? active : null
  const parts = EXPORT_PARTS.filter((p) => !p.owners || owner)
  const found = parts.find((p) => p.slug === (given ?? params.part))
  if (me.isPending || servers.isPending) return <Spinner />
  if (!found) return <Navigate to={`/settings/export/${parts[0].slug}`} replace />
  const cards: Record<ExportPart, () => ReactNode> = {
    history: () => (
      <>
        <LastFmImportCard isAdmin={Boolean(me.data?.is_admin)} />
        <ListenBrainzImportCard />
        <HistoryExportCard />
      </>
    ),
    library: () => <LibraryExportCard />,
    gigs: () => (
      <>
        <GigImportCard />
        <GigExportCard />
      </>
    ),
    logos: () =>
      owner && (
        <>
          <LogoPackImport serverId={owner.id} />
          <LogoPackExport serverId={owner.id} />
        </>
      ),
  }
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{found.about}</p>
      <div className="grid items-start gap-4 xl:grid-cols-2">{cards[found.slug]()}</div>
    </div>
  )
}

function LibraryExportCard() {
  const servers = useServers()
  if (servers.isPending) return <Spinner />
  const withLibrary = (servers.data ?? []).filter((s) => s.music_section_keys.length > 0)
  return (
    <Card title="Library, with your ratings and plays">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Every song with its album and artist, your rating, and play count. The JSON file also has artists and albums
          with their genres and your ratings.
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
  )
}

function HistoryExportCard() {
  return (
    <Card title="Export listening history">
      <div className="space-y-3 text-sm">
        <a className={linkButton} href="/api/listens/export.jsonl" download>
          Download all listens
        </a>
        <p className="text-xs text-muted">One file in ListenBrainz's format (JSON lines), oldest first, with each play once.</p>
      </div>
    </Card>
  )
}
