import { useMe } from '../api/hooks'
import { AcoustIdKeyCard } from '../components/AcoustIdKeyCard'
import { DataThistleCard } from '../components/DataThistleCard'
import { JamBaseKeyCard } from '../components/JamBaseKeyCard'
import { ScrobbleServicesCard } from '../components/ScrobbleServicesCard'
import { SetlistFmKeyCard } from '../components/SetlistFmKeyCard'
import { TicketmasterKeyCard } from '../components/TicketmasterKeyCard'
import { Spinner } from '../components/ui'
import { LastFmKeyCard, ListenBrainzCard } from './ListeningSettingsPage'
import { FanartKeyCard } from './SettingsPage'

/** Accounts and API keys for outside services: ListenBrainz for everyone, keys for admins. */
export function ConnectionsPage() {
  const me = useMe()
  if (me.isPending) return <Spinner />
  const isAdmin = Boolean(me.data?.is_admin)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <ListenBrainzCard />
        <ScrobbleServicesCard />
      </div>
      {isAdmin && (
        <div className="space-y-4">
          <LastFmKeyCard />
          <FanartKeyCard />
          <AcoustIdKeyCard />
          <JamBaseKeyCard />
          <TicketmasterKeyCard />
          <DataThistleCard />
          <SetlistFmKeyCard />
        </div>
      )}
    </div>
  )
}
