import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useActiveServer, useSyncStatus } from '../api/library'
import { SyncPanel } from './library'
import { Card, Notice, Spinner } from './ui'

/** Resolves the active server, or explains why the library can't be shown yet. */
export function useLibraryServer() {
  const { servers, active, choose } = useActiveServer()
  const sync = useSyncStatus(active?.id ?? null)
  let blocker: ReactNode = null
  if (servers.isPending || (active && sync.isPending)) blocker = <Spinner />
  else if (servers.isError) blocker = <Notice tone="error">{errorMessage(servers.error)}</Notice>
  else if (!active)
    blocker = (
      <Notice>
        Connect a Plex server in <Link to="/settings" className="underline">Settings</Link> first.
      </Notice>
    )
  else if (active.music_section_keys.length === 0)
    blocker = (
      <Notice>
        Pick a music library for {active.name} in <Link to="/settings" className="underline">Settings</Link>.
      </Notice>
    )
  else if (sync.data && sync.data.counts.tracks === 0)
    blocker = (
      <Card title="Setting up your library">
        <SyncPanel serverId={active.id} compact />
      </Card>
    )
  return { servers: servers.data ?? [], active, choose, blocker }
}

