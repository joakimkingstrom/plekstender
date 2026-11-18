import { Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useLogout, useMe } from '../api/hooks'
import { useTheme } from '../theme'
import { Avatar } from './ui'

const NAV = [
  { to: '/', label: 'Home', end: true },
  { to: '/library', label: 'Library' },
  { to: '/history', label: 'History' },
  { to: '/rate', label: 'Rate' },
  { to: '/artwork', label: 'Artwork' },
  { to: '/playlists', label: 'Playlists' },
  { to: '/settings', label: 'Settings' },
]

const PICK_PATH =
  'M32 4C48 4 59 9.5 59 22C59 35.5 44 52 35.5 58C33.4 59.5 30.6 59.5 28.5 58C20 52 5 35.5 5 22C5 9.5 16 4 32 4ZM32 15L34.94 22.45L42.94 22.95L36.76 28.05L38.76 35.8L32 31.5L25.24 35.8L27.24 28.05L21.06 22.95L29.06 22.45Z'

/** The guitar-pick mark (branding/plekstender-mark-mono.svg), coloured by `currentColor`. */
export function Mark({ className = 'size-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <path fill="currentColor" fillRule="evenodd" d={PICK_PATH} />
    </svg>
  )
}

export function Wordmark({ size = 'text-lg' }: { size?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 font-display font-bold tracking-tight ${size}`}>
      <Mark className="size-[1.15em] text-accent" />
      <span>plekstender</span>
    </span>
  )
}

/** Full-page loading state using the animated brand loader. */
export function PageLoader() {
  return (
    <div className="flex min-h-dvh items-center justify-center">
      <img src="/brand/plekstender-loader.svg" alt="Loading" className="size-16" />
    </div>
  )
}

/** Shell for signed-in pages; redirects to /login when signed out. */
export function Layout() {
  const { data: me, isPending } = useMe()
  const location = useLocation()
  const navigate = useNavigate()
  const logout = useLogout()
  const [theme, setTheme] = useTheme()

  if (isPending) {
    return <PageLoader />
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
      isActive ? 'bg-surface-2 text-text' : 'text-muted hover:text-text'
    }`

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-10 border-b border-border bg-bg/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <NavLink to="/" aria-label="Plekstender home">
            <Wordmark />
          </NavLink>
          <nav className="hidden gap-1 md:flex" aria-label="Main">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
                {item.label}
              </NavLink>
            ))}
            {me.is_admin && (
              <NavLink to="/admin" className={navClass}>
                Admin
              </NavLink>
            )}
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

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 pb-24 md:pb-6">
        <Outlet />
      </main>

      {/* Mobile bottom navigation */}
      <nav
        className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-7 border-t border-border bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
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
