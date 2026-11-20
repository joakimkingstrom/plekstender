import { NavLink } from 'react-router'

export type Tab = { to: string; label: string; end?: boolean }

/** A page's tabs (History, Library, Gigs): links under the title, the current one underlined. */
export function TabNav({ label, tabs, className = '' }: { label: string; tabs: Tab[]; className?: string }) {
  const tab = ({ isActive }: { isActive: boolean }) =>
    `border-b-2 px-1 pb-2 text-sm font-medium ${
      isActive ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'
    }`
  return (
    <nav
      className={`no-scrollbar mb-6 flex gap-6 overflow-x-auto whitespace-nowrap border-b border-border ${className}`}
      aria-label={label}
    >
      {tabs.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={tab}>
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}
