import { useEffect } from 'react'
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useLogout, useMe } from '../api/hooks'
import { useOnline } from '../online'
import { InstallPrompt } from './InstallPrompt'
import { applyAccent } from '../accent'
import { useTheme } from '../theme'
import { SPIN_MS, SPIN_SCALE } from '../brand'
import { Mark } from './Mark'
import { Avatar, Button } from './ui'

const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/library', label: 'Library' },
  { to: '/history', label: 'History' },
  { to: '/rate', label: 'Rate' },
  { to: '/artwork', label: 'Artwork' },
  { to: '/playlists', label: 'Playlists' },
  { to: '/settings', label: 'Settings' },
]

export function Wordmark({ size = 'text-lg' }: { size?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 font-display font-bold tracking-tight ${size}`}>
      <Mark className="size-[1.15em]" />
      <span>plekstender</span>
    </span>
  )
}

/** The pick spins once (on clicking the logo), unless the user prefers less motion. */
function spinPick(link: HTMLElement) {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  link.querySelector('svg')?.animate?.(
    SPIN_SCALE.map((x) => ({ transform: `scaleX(${x})` })),
    { duration: SPIN_MS, easing: 'linear' },
  )
}

/** Full-page loading state using the animated brand loader. */
export function PageLoader() {
  return (
    <div className="flex min-h-dvh items-center justify-center" role="status" aria-label="Loading">
      <Mark className="pick-spin size-16 text-text" />
    </div>
  )
}

/** Shell for signed-in pages; redirects to /login when signed out. */
export function Layout() {
  const { data: me, isPending, isError, refetch, isRefetching } = useMe()
  const online = useOnline()
  const location = useLocation()
  const navigate = useNavigate()
  const logout = useLogout()
  const [theme, setTheme] = useTheme()
  const accent = me?.preferences?.accent ?? null
  // The user's accent colour, worked out for the current theme.
  useEffect(() => applyAccent(accent, theme), [accent, theme])

  if (isPending) {
    return <PageLoader />
  }
  // Couldn't ask the server (offline, or it's down): that's not the same as signed out.
  if (isError && me === undefined) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <Mark className="size-12 text-text" />
        <h1 className="font-display text-xl font-bold">
          {online ? "Plekstender can't be reached" : "You're offline"}
        </h1>
        <p className="max-w-sm text-sm text-muted">
          {online
            ? 'The server is not answering right now. Check that it is running, then try again.'
            : 'Plekstender needs a connection to your server. It will work again as soon as you are back online.'}
        </p>
        <Button onClick={() => void refetch()} busy={isRefetching}>
          Try again
        </Button>
      </div>
    )
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  // On a phone the app opens on Rate (a preference). Only the page the app was opened on
  // (React Router's "default" location) is redirected, so Home stays reachable.
  if (
    location.key === 'default' &&
    location.pathname === '/' &&
    (me.preferences?.phone_start ?? 'rate') === 'rate' &&
    window.matchMedia?.('(max-width: 767px)').matches
  ) {
    return <Navigate to="/rate" replace />
  }

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
      isActive ? 'bg-surface-2 text-text' : 'text-muted hover:text-text'
    }`

  // On a phone, Rate takes the whole screen (no header or tab bar); it has its own close button.
  const focus = location.pathname === '/rate'
  const phoneHidden = focus ? 'max-md:hidden' : ''
  return (
    <div className="flex min-h-dvh flex-col">
      <header className={`sticky top-0 z-10 border-b border-border bg-bg/90 pt-[env(safe-area-inset-top)] backdrop-blur ${phoneHidden}`}>
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <NavLink to="/" aria-label="Plekstender home" onClick={(e) => spinPick(e.currentTarget)}>
            <Wordmark />
          </NavLink>
          <nav className="hidden gap-1 md:flex" aria-label="Main">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-text"
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            <Avatar url={me.thumb_url} name={me.username} />
            <button
              type="button"
              className="hidden rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-text sm:block"
              onClick={() => logout.mutate(undefined, { onSuccess: () => navigate('/login') })}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <InstallPrompt />
      {!online && (
        <div role="status" className="border-b border-warn/40 bg-warn/10 px-4 py-2 text-center text-xs text-warn">
          You're offline. What's on screen may be out of date, and changes can't be saved until you're back online.
        </div>
      )}
      <main
        className={`mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:pb-6 ${
          focus ? 'max-md:pb-[calc(env(safe-area-inset-bottom)+1rem)] max-md:pt-[calc(env(safe-area-inset-top)+0.5rem)]' : 'pb-24'
        }`}
      >
        <Outlet />
      </main>

      {/* Mobile bottom navigation */}
      <nav
        className={`fixed inset-x-0 bottom-0 z-10 grid grid-cols-7 border-t border-border bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden ${phoneHidden}`}
        aria-label="Main"
      >
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `py-3 text-center text-[10px] font-medium ${isActive ? 'text-accent' : 'text-muted'}`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
