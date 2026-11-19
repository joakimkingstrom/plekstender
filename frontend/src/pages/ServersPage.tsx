import { useState } from 'react'
import { Tooltip } from '../components/Tooltip'
import { errorMessage, type Schemas } from '../api/client'
import {
  useAvailableServers,
  useConnectServer,
  useDisconnectServer,
  useSections,
  useSelectSections,
  useServers,
} from '../api/hooks'
import { SyncPanel } from '../components/library'
import { Badge, Button, Card, Notice, PageTitle, Spinner } from '../components/ui'

const KIND_LABEL: Record<string, string> = {
  local: 'Local network',
  remote: 'Remote',
  relay: 'Plex relay (slow)',
}

function SectionPicker({ server }: { server: Schemas['ServerOut'] }) {
  const sections = useSections(server.id)
  const select = useSelectSections(server.id)

  if (sections.isPending) return <Spinner small />
  if (sections.isError) return <Notice tone="error">{errorMessage(sections.error)}</Notice>
  if (sections.data.length === 0) {
    return <Notice tone="warn">This server has no music libraries.</Notice>
  }

  const selected = new Set(server.music_section_keys)
  const toggle = (key: string) => {
    const next = new Set(selected)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    select.mutate([...next])
  }

  return (
    <fieldset className="space-y-2" disabled={select.isPending}>
      <legend className="mb-2 text-sm text-muted">Music libraries to use</legend>
      {sections.data.map((section) => (
        <label key={section.key} className="flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            className="size-4 accent-accent"
            checked={selected.has(section.key)}
            onChange={() => toggle(section.key)}
          />
          {section.title}
        </label>
      ))}
      {select.isError && <Notice tone="error">{errorMessage(select.error)}</Notice>}
    </fieldset>
  )
}

function ConnectedServer({ server }: { server: Schemas['ServerOut'] }) {
  const connect = useConnectServer()
  const disconnect = useDisconnectServer()
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {server.name}
          {server.is_owner && <Badge tone="accent">Owner</Badge>}
          <Badge>{KIND_LABEL[server.connection_kind] ?? server.connection_kind}</Badge>
        </span>
      }
      action={
        <div className="flex gap-1">
          <Tooltip content="Find the best address for this server again">
            <Button variant="ghost" busy={connect.isPending} onClick={() => connect.mutate(server.machine_identifier)}>
              Reconnect
            </Button>
          </Tooltip>
          <Button
            variant="danger"
            busy={disconnect.isPending}
            onClick={() => {
              if (window.confirm(`Disconnect ${server.name} from Plekstender?`)) {
                disconnect.mutate(server.id)
              }
            }}
          >
            Disconnect
          </Button>
        </div>
      }
    >
      <SectionPicker server={server} />
      {server.music_section_keys.length > 0 && (
        <div className="mt-5 space-y-4 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Library sync</h3>
          <SyncPanel serverId={server.id} />
          <div className="text-sm">
            <span className="text-muted">Export your library with your ratings and plays: </span>
            <a className="text-accent underline" href={`/api/servers/${server.id}/export/tracks.csv`} download>
              tracks (CSV)
            </a>
            <span className="text-muted"> · </span>
            <a className="text-accent underline" href={`/api/servers/${server.id}/export/library.json`} download>
              full library (JSON)
            </a>
          </div>
        </div>
      )}
      {connect.isError && (
        <div className="mt-3">
          <Notice tone="error">{errorMessage(connect.error)}</Notice>
        </div>
      )}
    </Card>
  )
}

function AvailableServers() {
  const available = useAvailableServers()
  const connect = useConnectServer()
  const [connecting, setConnecting] = useState<string | null>(null)

  if (available.isPending) return <Spinner />
  if (available.isError) return <Notice tone="error">{errorMessage(available.error)}</Notice>

  const unconnected = available.data.filter((s) => !s.connected)
  if (unconnected.length === 0) {
    return (
      <p className="text-sm text-muted">
        {available.data.length === 0
          ? 'No Plex servers were found on your account.'
          : 'All your servers are connected.'}
      </p>
    )
  }

  return (
    <ul className="divide-y divide-border">
      {unconnected.map((server) => (
        <li key={server.machine_identifier} className="flex items-center gap-3 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 font-medium">
              {server.name}
              {server.owned && <Badge tone="accent">Owner</Badge>}
              {!server.online && <Badge>Offline</Badge>}
            </div>
            {!server.allowed && (
              <p className="text-xs text-muted">Not approved by this instance&apos;s admin.</p>
            )}
          </div>
          <Button
            variant="primary"
            disabled={!server.allowed}
            busy={connect.isPending && connecting === server.machine_identifier}
            onClick={() => {
              setConnecting(server.machine_identifier)
              connect.mutate(server.machine_identifier)
            }}
          >
            Connect
          </Button>
        </li>
      ))}
      {connect.isError && (
        <li className="pt-3">
          <Notice tone="error">{errorMessage(connect.error)}</Notice>
        </li>
      )}
    </ul>
  )
}

export function ServersPage({ firstRun = false }: { firstRun?: boolean }) {
  const servers = useServers()

  return (
    <>
      {firstRun ? (
        <PageTitle subtitle="Connect a Plex server and pick the music library Plekstender should work with.">
          Get started
        </PageTitle>
      ) : (
        <p className="mb-4 text-sm text-muted">Plex servers connected to Plekstender.</p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          {servers.isPending && <Spinner />}
          {servers.isError && <Notice tone="error">{errorMessage(servers.error)}</Notice>}
          {servers.data?.map((server) => (
            <ConnectedServer key={server.id} server={server} />
          ))}
        </div>
        <Card title="Your Plex servers">
          <AvailableServers />
        </Card>
      </div>
    </>
  )
}
