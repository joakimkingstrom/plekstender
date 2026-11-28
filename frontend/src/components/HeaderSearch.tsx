import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import type { Schemas } from '../api/client'
import { useActiveServer, useLibrarySearch } from '../api/library'
import { Artwork } from './library'

type Hit = Schemas['SearchHit'] & { kind: 'artist' | 'album' | 'song' }

const GROUPS: [keyof Schemas['LibrarySearchOut'], Hit['kind'], string][] = [
  ['artists', 'artist', 'Artists'],
  ['albums', 'album', 'Albums'],
  ['songs', 'song', 'Songs'],
]

function hrefOf(hit: Hit): string {
  if (hit.kind === 'artist') return `/library/artists/${hit.rating_key}`
  if (hit.kind === 'album') return `/library/albums/${hit.rating_key}`
  return hit.album_rating_key ? `/library/albums/${hit.album_rating_key}` : '/library'
}

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

/** The search field and its results (a listbox under it): ↑ ↓ move, Enter opens the one
 * picked (the first when none is), Escape closes. */
function SearchField({ autoFocus = false, onDone, className = '' }: { autoFocus?: boolean; onDone?: () => void; className?: string }) {
  const { active } = useActiveServer()
  const navigate = useNavigate()
  const id = useId()
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const q = useDebounced(text.trim(), 200)
  const found = useLibrarySearch(active?.id ?? null, q)
  const hits: Hit[] = found.data ? GROUPS.flatMap(([key, kind]) => found.data![key].map((h) => ({ ...h, kind }))) : []
  const shown = open && q.length > 0 && text.trim().length > 0

  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])

  const go = (hit: Hit | undefined) => {
    if (!hit) return
    navigate(hrefOf(hit))
    setText('')
    setOpen(false)
    onDone?.()
  }
  const keys = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      if (hits.length) setAt((i) => (i + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      go(hits[at] ?? hits[0])
    } else if (e.key === 'Escape') {
      if (text) setText('')
      else onDone?.()
      setOpen(false)
    }
  }

  return (
    <div ref={root} className={`relative ${className}`}>
      <input
        type="search"
        role="combobox"
        aria-label="Search the library"
        aria-expanded={shown}
        aria-controls={`${id}-list`}
        aria-activedescendant={shown && hits[at] ? `${id}-${at}` : undefined}
        aria-autocomplete="list"
        autoFocus={autoFocus}
        placeholder="Search"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setAt(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={keys}
        className="w-full rounded-lg border border-border bg-surface-2 px-3 py-1.5 text-sm"
      />
      {shown && (
        <div
          id={`${id}-list`}
          role="listbox"
          aria-label="Search results"
          className="absolute right-0 top-full z-30 mt-2 max-h-[70vh] w-full min-w-72 overflow-y-auto rounded-2xl border border-border bg-surface p-2 text-sm shadow-lg"
        >
          {found.isPending ? (
            <p className="px-2 py-1.5 text-muted">Searching…</p>
          ) : hits.length === 0 ? (
            <p className="px-2 py-1.5 text-muted">Nothing called that.</p>
          ) : (
            GROUPS.map(([key, kind, label]) => {
              const group = hits.filter((h) => h.kind === kind)
              if (group.length === 0) return null
              return (
                <div key={key} role="group" aria-label={label}>
                  <p className="px-2 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
                  {group.map((hit) => {
                    const i = hits.indexOf(hit)
                    return (
                      <div
                        key={`${kind}-${hit.rating_key}`}
                        id={`${id}-${i}`}
                        role="option"
                        aria-selected={i === at}
                        onPointerDown={(e) => e.preventDefault()}
                        onClick={() => go(hit)}
                        onPointerEnter={() => setAt(i)}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 ${i === at ? 'bg-surface-2' : ''}`}
                      >
                        {active && (
                          <Artwork serverId={active.id} path={hit.thumb} title={hit.title} size={96} round={kind === 'artist'} className="size-8 shrink-0" />
                        )}
                        <span className="min-w-0">
                          <span className="block truncate">{hit.title}</span>
                          {hit.subtitle && <span className="block truncate text-xs text-muted">{hit.subtitle}</span>}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}

/** The header's library search: a field on wide screens; on phones an icon that opens a
 * full-width field. */
export function HeaderSearch() {
  const [phoneOpen, setPhoneOpen] = useState(false)
  return (
    <>
      <SearchField className="hidden w-48 md:block lg:w-56" />
      <button
        type="button"
        aria-label="Search"
        title="Search"
        onClick={() => setPhoneOpen(true)}
        className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text md:hidden"
      >
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
          <circle cx="11" cy="11" r="6.5" />
          <path d="M16 16l4.5 4.5" />
        </svg>
      </button>
      {phoneOpen && (
        <div className="fixed inset-x-0 top-0 z-30 flex items-start gap-2 border-b border-border bg-bg p-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] md:hidden">
          <SearchField autoFocus onDone={() => setPhoneOpen(false)} className="flex-1" />
          <button type="button" onClick={() => setPhoneOpen(false)} className="rounded-lg px-2 py-1.5 text-sm text-muted hover:text-text">
            Close
          </button>
        </div>
      )}
    </>
  )
}
