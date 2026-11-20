import { Navigate, Outlet, useLocation, useParams } from 'react-router'
import { useActiveServer } from '../api/library'
import { TabNav } from '../components/TabNav'
import { PageTitle } from '../components/ui'
import { ServerPicker } from './LibraryPage'

/** Library: the artists and albums, and what's done with them (playlists, rating, artwork). */
export function LibraryLayout() {
  const { servers, active, choose } = useActiveServer()
  // Rate fills a phone's screen on its own (see Layout), so the title and tabs step aside.
  const focus = useLocation().pathname === '/library/rate'
  return (
    <>
      <div className={`mb-2 flex flex-wrap items-end justify-between gap-3 ${focus ? 'max-md:hidden' : ''}`}>
        <PageTitle>Library</PageTitle>
        {active && <ServerPicker servers={servers.data ?? []} active={active} choose={choose} />}
      </div>
      <TabNav
        label="Library"
        className={focus ? 'max-md:hidden' : ''}
        tabs={[
          { to: '/library', label: 'Library', end: true },
          { to: '/library/playlists', label: 'Playlists' },
          { to: '/library/rate', label: 'Rate' },
          ...(active?.is_owner ? [{ to: '/library/artwork', label: 'Artwork' }] : []),
        ]}
      />
      <Outlet />
    </>
  )
}

/** Gigs: the ones coming up near you, and the ones you went to. */
export function GigsLayout() {
  return (
    <>
      <PageTitle>Gigs</PageTitle>
      <TabNav
        label="Gigs"
        tabs={[
          { to: '/gigs', label: 'Upcoming', end: true },
          { to: '/gigs/been-to', label: 'Been to' },
        ]}
      />
      <Outlet />
    </>
  )
}

/** Old addresses lead to the new ones (bookmarks, the installed app's start page). */
export function PlaylistRedirect() {
  const { id } = useParams()
  return <Navigate to={id ? `/library/playlists/${id}` : '/library/playlists'} replace />
}
