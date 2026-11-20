import type { ReactNode } from 'react'
import { Navigate } from 'react-router'
import { useMe } from '../api/hooks'
import { AcoustIdKeyCard } from '../components/AcoustIdKeyCard'
import { DataThistleCard } from '../components/DataThistleCard'
import { JamBaseKeyCard } from '../components/JamBaseKeyCard'
import { ScrobbleServicesCard } from '../components/ScrobbleServicesCard'
import { SetlistFmKeyCard } from '../components/SetlistFmKeyCard'
import { SpotifyAppCard } from '../components/SpotifyAppCard'
import { TicketmasterKeyCard } from '../components/TicketmasterKeyCard'
import { Spinner } from '../components/ui'
import { LastFmKeyCard, ListenBrainzCard } from './ListeningSettingsPage'
import { FanartKeyCard } from './SettingsPage'

/** Settings → Scrobbling: where your new Plex plays are sent. */
export function ScrobblingPage() {
  return (
    <div className="grid items-start gap-4 xl:grid-cols-2">
      <ListenBrainzCard />
      <ScrobbleServicesCard />
    </div>
  )
}

function Group({ title, about, children }: { title: string; about: string; children: ReactNode }) {
  return (
    <section className="space-y-3" aria-label={title}>
      <div>
        <h3 className="font-semibold">{title}</h3>
        <p className="text-sm text-muted">{about}</p>
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-2">{children}</div>
    </section>
  )
}

/** Settings → Services (admins): every outside service's key or app, by what it's for. */
export function ServicesPage() {
  const me = useMe()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  return (
    <div className="space-y-8">
      <Group title="Gigs" about="Upcoming gigs near people (Gigs → Upcoming), and importing the ones they went to.">
        <JamBaseKeyCard />
        <TicketmasterKeyCard />
        <DataThistleCard />
        <SetlistFmKeyCard />
      </Group>
      <Group title="History" about="Importing listening history.">
        <LastFmKeyCard />
      </Group>
      <Group title="Playlists" about="Importing playlists from Spotify links.">
        <SpotifyAppCard />
      </Group>
      <Group title="Artwork" about="Artist logos for the Artwork tab.">
        <FanartKeyCard />
      </Group>
      <Group title="Music files" about="Identifying music files by how they sound.">
        <AcoustIdKeyCard />
      </Group>
    </div>
  )
}
