import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState, type CSSProperties } from 'react'
import { Link } from 'react-router'
import { api, unwrap, type Schemas } from '../api/client'
import { imageUrl, useActiveServer } from '../api/library'
import { useReducedMotion } from '../motion'
import { Artwork } from './library'
import { Tooltip } from './Tooltip'
import type { Theme } from '../theme'

export type BackgroundSettings = Schemas['BackgroundSettings']

/** How long each cover stays, by speed (seconds); the next one fades in over FADE. */
const SECONDS: Record<NonNullable<BackgroundSettings['speed']>, number> = { slow: 30, normal: 22, fast: 14 }
const FADE = 2.5
const SIZE = 1000 // px: the Plex image proxy's size for a full-window cover

/** Where a cover's slow pan starts and ends (percent), different for each one shown. */
function drift(n: number) {
  const at = (k: number) => ((Math.sin(n * 12.9898 + k * 78.233) * 43758.5453) % 1) * 4
  return { '--kb-x0': `${at(1)}%`, '--kb-y0': `${at(2)}%`, '--kb-x1': `${-at(3)}%`, '--kb-y1': `${-at(4)}%` }
}

/**
 * Album covers slowly panning and zooming behind the pages (Ken Burns), one after another
 * with a cross-fade, dimmed so the page stays readable: light covers in light mode and dark
 * ones in dark mode, or one colour family's. Nothing at all when it's off or the system asks
 * for less motion.
 */
export function CoverBackground({ theme, settings }: { theme: Theme; settings: BackgroundSettings | undefined }) {
  const { active } = useActiveServer()
  const reduced = useReducedMotion()
  const mode = settings?.mode ?? 'off'
  const family = settings?.family ?? 'blue'
  const seconds = SECONDS[settings?.speed ?? 'normal']
  const on = mode !== 'off' && !reduced && active !== undefined
  const covers = useQuery({
    queryKey: ['servers', active?.id, 'backgrounds', mode, mode === 'colour' ? family : theme],
    enabled: on,
    staleTime: Infinity,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/stats/backgrounds', {
          params: {
            path: { server_id: active!.id },
            query: { mode: mode === 'colour' ? 'colour' : 'covers', theme, family },
          },
        }),
      ),
  })
  const list = covers.data ?? []
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!on || list.length < 2) return
    const timer = window.setInterval(() => setN((i) => i + 1), seconds * 1000)
    return () => window.clearInterval(timer)
  }, [on, list.length, seconds])
  const url = (i: number) => (active && list.length ? imageUrl(active.id, list[i % list.length].thumb, SIZE) : null)
  // The next cover, loaded before it's needed.
  useEffect(() => {
    const next = url(n + 1)
    if (next) new Image().src = next
  })
  if (!on || list.length === 0) return null
  // The cover showing and the one before it (fading out under it).
  const shown = n > 0 ? [n - 1, n] : [n]
  return (
    <>
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" data-testid="cover-background">
        {shown.map((i) => (
          <div
            key={i}
            className="cover-slide absolute inset-0"
            style={{ animationDuration: `${FADE}s, ${seconds + FADE}s`, ...drift(i) } as CSSProperties}
          >
            <img src={url(i)!} alt="" className="size-full object-cover" />
          </div>
        ))}
        <div className="absolute inset-0 bg-bg/85" />
      </div>
      {active && <BackgroundInfo serverId={active.id} cover={list[n % list.length]} />}
    </>
  )
}

/** A small button in the bottom right corner that opens what the background is: its cover,
 * artist and album (artist first), each a link. Follows the covers as they change. */
function BackgroundInfo({ serverId, cover }: { serverId: number; cover: Schemas['BackgroundCover'] }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const album = `/library/albums/${cover.rating_key}`
  return (
    <div className="fixed bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] right-3 z-20 md:bottom-4 md:right-4">
      {open ? (
        <div
          id={id}
          role="dialog"
          aria-label="The background"
          className="flex w-72 max-w-[calc(100vw-1.5rem)] items-center gap-3 rounded-2xl border border-border bg-surface/95 p-2 text-sm shadow-lg backdrop-blur"
        >
          <Link to={album} className="shrink-0" aria-label={cover.title || 'The album'}>
            <Artwork serverId={serverId} path={cover.thumb} title={cover.title} size={128} className="size-12 rounded" />
          </Link>
          <span className="min-w-0 flex-1">
            {cover.artist &&
              (cover.artist_rating_key ? (
                <Link to={`/library/artists/${cover.artist_rating_key}`} className="block truncate text-xs text-muted hover:text-text hover:underline">
                  {cover.artist}
                </Link>
              ) : (
                <span className="block truncate text-xs text-muted">{cover.artist}</span>
              ))}
            <Link to={album} className="block truncate font-medium hover:underline">
              {cover.title || 'This album'}
            </Link>
          </span>
          <button
            type="button"
            aria-label="Close"
            onClick={() => setOpen(false)}
            className="flex size-7 shrink-0 items-center justify-center self-start rounded-full text-muted hover:bg-surface-2 hover:text-text"
          >
            ×
          </button>
        </div>
      ) : (
        <Tooltip content="What’s in the background?">
          <button
            type="button"
            aria-label="What’s in the background?"
            aria-expanded={false}
            onClick={() => setOpen(true)}
            className="flex size-8 items-center justify-center rounded-full border border-border bg-surface/80 text-muted shadow backdrop-blur hover:text-text"
          >
            <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
              <circle cx="12" cy="12" r="4" />
              <circle cx="12" cy="12" r="0.8" fill="currentColor" />
            </svg>
          </button>
        </Tooltip>
      )}
    </div>
  )
}
