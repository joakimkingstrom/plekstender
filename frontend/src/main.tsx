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
import { ConnectionsPage } from './pages/ConnectionsPage'
import { ExportPage } from './pages/ExportPage'
import { HistoryLayout, ListensPage } from './pages/HistoryPage'
import { HomePage, NotFoundPage } from './pages/HomePage'
import { AlbumPage, ArtistPage, LibraryPage } from './pages/LibraryPage'
import { ListeningSettingsPage } from './pages/ListeningSettingsPage'
import { LivePage } from './pages/LivePage'
import { GameSettingsPage } from './pages/GameSettingsPage'
import { MapPage } from './pages/MapPage'
import { PlaylistEditorPage, PlaylistsPage } from './pages/PlaylistsPage'
import { RatePage } from './pages/RatePage'
import { RatingsPage } from './pages/RatingsPage'
import { ScoresPage } from './pages/ScoresPage'
import { LoginCallbackPage, LoginPage } from './pages/LoginPage'
import { ServersPage } from './pages/ServersPage'
import { AppearancePage, ArtworkSettingsPage, FilesSettingsPage, LibrarySettingsPage, SettingsLayout } from './pages/SettingsPage'
import { StatsPage } from './pages/StatsPage'

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
      { path: 'library', element: <LibraryPage /> },
      { path: 'library/artists/:key', element: <ArtistPage /> },
      { path: 'library/albums/:key', element: <AlbumPage /> },
      {
        path: 'history',
        element: <HistoryLayout />,
        children: [
          { index: true, element: <StatsPage /> },
          { path: 'listens', element: <ListensPage /> },
          { path: 'map', element: <MapPage /> },
          { path: 'ratings', element: <RatingsPage /> },
          { path: 'scores', element: <ScoresPage /> },
        ],
      },
      {
        path: 'rate',
        element: <RatePage />,
      },
      {
        path: 'artwork',
        element: <ArtworkPage />,
      },
      { path: 'playlists', element: <PlaylistsPage /> },
      { path: 'playlists/new', element: <PlaylistEditorPage /> },
      { path: 'playlists/:id', element: <PlaylistEditorPage /> },
      { path: 'live', element: <LivePage /> },
      {
        path: 'settings',
        element: <SettingsLayout />,
        children: [
          { index: true, element: <ServersPage /> },
          { path: 'listening', element: <ListeningSettingsPage /> },
          { path: 'connections', element: <ConnectionsPage /> },
          { path: 'export', element: <ExportPage /> },
          { path: 'appearance', element: <AppearancePage /> },
          { path: 'artwork', element: <ArtworkSettingsPage /> },
          { path: 'files', element: <FilesSettingsPage /> },
          { path: 'game', element: <GameSettingsPage /> },
          { path: 'library', element: <LibrarySettingsPage /> },
          { path: 'admin', element: <Navigate to="/settings" replace /> },
        ],
      },
      { path: 'admin', element: <Navigate to="/settings" replace /> },
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
