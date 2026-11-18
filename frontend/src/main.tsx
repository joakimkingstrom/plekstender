import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource/bricolage-grotesque/600.css'
import '@fontsource/bricolage-grotesque/700.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { ApiError } from './api/client'
import { Layout } from './components/Layout'
import './index.css'
import { AdminPage } from './pages/AdminPage'
import { ArtworkPage } from './pages/ArtworkPage'
import { HistoryLayout, ListensPage } from './pages/HistoryPage'
import { ComingSoonPage, HomePage, NotFoundPage } from './pages/HomePage'
import { AlbumPage, ArtistPage, LibraryPage } from './pages/LibraryPage'
import { ListeningSettingsPage } from './pages/ListeningSettingsPage'
import { RatePage } from './pages/RatePage'
import { LoginCallbackPage, LoginPage } from './pages/LoginPage'
import { ServersPage } from './pages/ServersPage'
import { LibrarySettingsPage, SettingsLayout } from './pages/SettingsPage'
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
      {
        path: 'playlists',
        element: <ComingSoonPage title="Playlists" text="The playlist builder arrives in a later phase." />,
      },
      {
        path: 'settings',
        element: <SettingsLayout />,
        children: [
          { index: true, element: <ServersPage /> },
          { path: 'listening', element: <ListeningSettingsPage /> },
          { path: 'library', element: <LibrarySettingsPage /> },
        ],
      },
      { path: 'admin', element: <AdminPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
