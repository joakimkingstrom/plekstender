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
import { ComingSoonPage, HomePage, NotFoundPage } from './pages/HomePage'
import { LoginCallbackPage, LoginPage } from './pages/LoginPage'
import { ServersPage } from './pages/ServersPage'

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
        path: 'rate',
        element: <ComingSoonPage title="Rate" text="Rating arrives in a later phase. It needs the library sync first." />,
      },
      {
        path: 'artwork',
        element: <ComingSoonPage title="Artwork" text="The artist artwork editor arrives in a later phase." />,
      },
      {
        path: 'playlists',
        element: <ComingSoonPage title="Playlists" text="The playlist builder arrives in a later phase." />,
      },
      { path: 'settings', element: <ServersPage /> },
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
