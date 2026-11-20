import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router'
import { useMe } from '../api/hooks'
import { AcoustIdKeyCard } from '../components/AcoustIdKeyCard'
import { DataThistleCard } from '../components/DataThistleCard'
import { JamBaseKeyCard } from '../components/JamBaseKeyCard'
import { ScrobbleServicesCard } from '../components/ScrobbleServicesCard'
import { SetlistFmKeyCard } from '../components/SetlistFmKeyCard'
import { SpotifyAppCard } from '../components/SpotifyAppCard'
import { TicketmasterKeyCard } from '../components/TicketmasterKeyCard'
import { Spinner } from '../components/ui'
import { SERVICE_KINDS, type ServiceKind } from '../settingsSections'
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

/** The keys and apps for each kind of service. */
const CARDS: Record<ServiceKind, () => ReactNode> = {
  gigs: () => (
    <>
      <JamBaseKeyCard />
      <TicketmasterKeyCard />
      <DataThistleCard />
      <SetlistFmKeyCard />
    </>
  ),
  history: () => <LastFmKeyCard />,
  playlists: () => <SpotifyAppCard />,
  artwork: () => <FanartKeyCard />,
  files: () => <AcoustIdKeyCard />,
}

/** Settings → Services → one kind (admins): the keys and apps for it. */
export function ServicesPage() {
  const me = useMe()
  const { kind } = useParams()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  const found = SERVICE_KINDS.find((k) => k.slug === kind)
  if (!found) return <Navigate to={`/settings/services/${SERVICE_KINDS[0].slug}`} replace />
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{found.about}</p>
      <div className="grid items-start gap-4 xl:grid-cols-2">{CARDS[found.slug]()}</div>
    </div>
  )
}
