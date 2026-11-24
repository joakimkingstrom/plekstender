import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource/bricolage-grotesque/600.css'
import '@fontsource/bricolage-grotesque/700.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { ApiError } from './api/client'
import { Layout } from './components/Layout'
import './index.css'
import { registerServiceWorker } from './online'
import { ArtworkPage } from './pages/ArtworkPage'
import { ScrobblingPage, ServicesPage } from './pages/ConnectionsPage'
import { UsersPage } from './pages/AdminPage'
import { ExportPage } from './pages/ExportPage'
import { HistoryLayout, ListensPage } from './pages/HistoryPage'
import { HomePage, NotFoundPage } from './pages/HomePage'
import { AlbumPage, ArtistPage, LibraryPage } from './pages/LibraryPage'
import { LibraryStatsPage } from './pages/LibraryStatsPage'
import { ListeningSettingsPage } from './pages/ListeningSettingsPage'
import { LivePage } from './pages/LivePage'
import { GameSettingsPage } from './pages/GameSettingsPage'
import { GigsPage } from './pages/GigsPage'
import { MapPage } from './pages/MapPage'
import { PlaylistEditorPage, PlaylistsPage } from './pages/PlaylistsPage'
import { RatePage } from './pages/RatePage'
import { ProfilePage } from './pages/ProfilePage'
import { RatingsPage } from './pages/RatingsPage'
import { RediscoverPage } from './pages/RediscoverPage'
import { RewindPage } from './pages/RewindPage'
import { ScoresPage } from './pages/ScoresPage'
import { TastePage } from './pages/TastePage'
import { LoginCallbackPage, LoginPage } from './pages/LoginPage'
import { ServersPage } from './pages/ServersPage'
import { FilesSettingsPage, FontsSettingsPage, LibrarySettingsPage, SettingsLayout } from './pages/SettingsPage'
import { StatsPage } from './pages/StatsPage'
import { DiscoverPage } from './pages/DiscoverPage'
import { TagEditingPage } from './pages/TagEditingPage'
import { TagPage } from './pages/TagPage'
import { GigsLayout, LibraryLayout, PlaylistRedirect } from './pages/Sections'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't retry client errors (401/403/404...), only network/server hiccups.
      retry: (count, error) =>
        !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
})

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/login/callback', element: <LoginCallbackPage /> },
  {
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      {
        path: 'library',
        element: <LibraryLayout />,
        children: [
          { index: true, element: <LibraryPage /> },
          { path: 'stats', element: <LibraryStatsPage /> },
          { path: 'playlists', element: <PlaylistsPage /> },
          { path: 'playlists/new', element: <PlaylistEditorPage /> },
          { path: 'playlists/:id', element: <PlaylistEditorPage /> },
          { path: 'rate', element: <RatePage /> },
          { path: 'discover', element: <DiscoverPage /> },
          { path: 'artwork', element: <ArtworkPage /> },
          { path: 'artwork/albums', element: <ArtworkPage /> },
          { path: 'tag-editing', element: <TagEditingPage /> },
        ],
      },
      { path: 'library/artists/:key', element: <ArtistPage /> },
      { path: 'library/albums/:key', element: <AlbumPage /> },
      { path: 'library/tags/:id', element: <TagPage /> },
      {
        path: 'gigs',
        element: <GigsLayout />,
        children: [
          { index: true, element: <LivePage /> },
          { path: 'been-to', element: <GigsPage /> },
        ],
      },
      {
        path: 'history',
        element: <HistoryLayout />,
        children: [
          { index: true, element: <StatsPage /> },
          { path: 'scrobbles', element: <ListensPage /> },
          { path: 'listens', element: <Navigate to="/history/scrobbles" replace /> },
          { path: 'map', element: <MapPage /> },
          { path: 'taste', element: <TastePage /> },
          { path: 'rediscover', element: <RediscoverPage /> },
          { path: 'gigs', element: <Navigate to="/gigs/been-to" replace /> },
          { path: 'ratings', element: <RatingsPage /> },
          { path: 'scores', element: <ScoresPage /> },
          { path: 'rewind', element: <RewindPage /> },
        ],
      },
      // Old addresses (bookmarks, an installed app's start page).
      { path: 'rate', element: <Navigate to="/library/rate" replace /> },
      { path: 'artwork', element: <Navigate to="/library/artwork" replace /> },
      { path: 'playlists', element: <PlaylistRedirect /> },
      { path: 'playlists/new', element: <Navigate to="/library/playlists/new" replace /> },
      { path: 'playlists/:id', element: <PlaylistRedirect /> },
      { path: 'live', element: <Navigate to="/gigs" replace /> },
      {
        path: 'settings',
        element: <SettingsLayout />,
        children: [
          { index: true, element: <ServersPage /> },
          { path: 'listening', element: <ListeningSettingsPage /> },
          { path: 'scrobbling', element: <ScrobblingPage /> },
          { path: 'services', element: <ServicesPage /> },
          { path: 'services/:kind', element: <ServicesPage /> },
          { path: 'users', element: <UsersPage /> },
          { path: 'connections', element: <Navigate to="/settings/scrobbling" replace /> },
          { path: 'export', element: <ExportPage /> },
          { path: 'export/:part', element: <ExportPage /> },
          { path: 'appearance', element: <Navigate to="/profile" replace /> },
          { path: 'artwork', element: <Navigate to="/settings/fonts/offered" replace /> },
          { path: 'fonts', element: <FontsSettingsPage /> },
          { path: 'fonts/:part', element: <FontsSettingsPage /> },
          { path: 'files', element: <FilesSettingsPage /> },
          { path: 'game', element: <GameSettingsPage /> },
          { path: 'library', element: <LibrarySettingsPage /> },
          { path: 'admin', element: <Navigate to="/settings" replace /> },
        ],
      },
      { path: 'admin', element: <Navigate to="/settings" replace /> },
      { path: 'profile', element: <ProfilePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

registerServiceWorker()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
