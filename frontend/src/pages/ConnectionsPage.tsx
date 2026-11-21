import type { ReactNode } from 'react'
import { Navigate, useParams, useSearchParams } from 'react-router'
import { useMe } from '../api/hooks'
import { AcoustIdKeyCard } from '../components/AcoustIdKeyCard'
import { DataThistleCard } from '../components/DataThistleCard'
import { JamBaseKeyCard } from '../components/JamBaseKeyCard'
import { ScrobbleServicesCard } from '../components/ScrobbleServicesCard'
import { SetlistFmKeyCard } from '../components/SetlistFmKeyCard'
import { SpotifyAppCard } from '../components/SpotifyAppCard'
import { TicketmasterKeyCard } from '../components/TicketmasterKeyCard'
import { TidalCard } from '../components/TidalCard'
import { LastFmKeyCard } from '../components/LastFmKeyCard'
import { Notice, Spinner } from '../components/ui'
import { SERVICE_KINDS, type ServiceKind } from '../settingsSections'
import { ListenBrainzCard } from './ListeningSettingsPage'
import { FanartKeyCard } from './SettingsPage'

/** What came back from connecting Last.fm (`?lastfm=` on return from last.fm). */
const LASTFM_RESULT: Record<string, { tone?: 'error'; text: string }> = {
  connected: { text: 'Last.fm is connected. Your new Plex plays will be scrobbled there.' },
  refused: { tone: 'error', text: 'Last.fm didn’t confirm the sign-in. Try connecting again.' },
  expired: { tone: 'error', text: 'That Last.fm sign-in had expired or wasn’t started here. Try connecting again.' },
  unavailable: { tone: 'error', text: 'Last.fm scrobbling isn’t set up on this server (an admin needs to add the API key and its shared secret).' },
  failed: { tone: 'error', text: 'Last.fm couldn’t be reached. Try again in a moment.' },
  full: { tone: 'error', text: 'You already have the most scrobble services allowed. Remove one to add Last.fm.' },
}

/** Settings → Scrobbling: where your new Plex plays are sent (and, for admins, the Last.fm key). */
export function ScrobblingPage() {
  const me = useMe()
  const [params] = useSearchParams()
  const result = LASTFM_RESULT[params.get('lastfm') ?? '']
  return (
    <div className="space-y-4">
      {result && <Notice tone={result.tone}>{result.text}</Notice>}
      <div className="grid items-start gap-4 xl:grid-cols-2">
        <ListenBrainzCard />
        <ScrobbleServicesCard />
        {me.data?.is_admin && <LastFmKeyCard />}
      </div>
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
  playlists: () => (
    <>
      <SpotifyAppCard />
      <TidalCard />
    </>
  ),
  artwork: () => (
    <>
      <FanartKeyCard />
      <TidalCard />
    </>
  ),
  files: () => <AcoustIdKeyCard />,
}

/** Settings → Services → one kind (admins): the keys and apps for it. */
export function ServicesPage() {
  const me = useMe()
  const { kind } = useParams()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  // The Last.fm key moved from Services → History to Scrobbling.
  if (kind === 'history') return <Navigate to="/settings/scrobbling" replace />
  const found = SERVICE_KINDS.find((k) => k.slug === kind)
  if (!found) return <Navigate to={`/settings/services/${SERVICE_KINDS[0].slug}`} replace />
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{found.about}</p>
      <div className="grid items-start gap-4 xl:grid-cols-2">{CARDS[found.slug]()}</div>
    </div>
  )
}
