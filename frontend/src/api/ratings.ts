import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, unwrap, type Schemas } from './client'
import { libraryKeys } from './library'

export type RateKind = Schemas['RateQueueIn']['kind']
export type RatePool = NonNullable<Schemas['RateQueueIn']['pool']>
export type RateOrder = NonNullable<Schemas['RateQueueIn']['order']>
export type RateItem = Schemas['RateItemOut']

const PAGE = 20
const REFILL_BELOW = 6

type QueueState = {
  /** The settings these items belong to; a change of settings starts a new queue. */
  key: string
  items: RateItem[]
  exhausted: boolean
  error: unknown
}

/**
 * A client-side queue of items to rate. Pages are fetched as it runs low; everything
 * already shown is sent back as `exclude`, so nothing repeats within a session.
 */
export function useRateQueue(
  serverId: number,
  kind: RateKind,
  pool: RatePool,
  order: RateOrder,
  /** Fetch more while fewer than this many items are in hand. */
  keep = REFILL_BELOW,
  /** Only items with this genre. */
  tagId: number | null = null,
) {
  const key = `${serverId}|${kind}|${pool}|${order}|${tagId ?? ''}`
  const [state, setState] = useState<QueueState>({ key: '', items: [], exhausted: false, error: null })
  // Bumped when a request finishes without changing the queue (e.g. settings changed while
  // it was in flight), so the effect below looks again.
  const [tick, setTick] = useState(0)
  const inFlight = useRef(false)
  const seen = useRef<{ key: string; keys: Set<number> }>({ key: '', keys: new Set() })

  const fetchPage = useCallback(async () => {
    if (inFlight.current) return
    if (seen.current.key !== key) seen.current = { key, keys: new Set() }
    const shown = seen.current.keys
    inFlight.current = true
    let changed = false
    try {
      const page = await unwrap(
        api.POST('/api/servers/{server_id}/rate/queue', {
          params: { path: { server_id: serverId } },
          body: { kind, pool, order, exclude: [...shown].slice(-5000), limit: PAGE, tag_id: tagId },
        }),
      )
      if (seen.current.key !== key) return // settings changed meanwhile
      const fresh = page.filter((i) => !shown.has(i.rating_key))
      fresh.forEach((i) => shown.add(i.rating_key))
      changed = true
      setState((s) => ({
        key,
        items: s.key === key ? [...s.items, ...fresh] : fresh,
        exhausted: page.length < PAGE,
        error: null,
      }))
    } catch (e) {
      if (seen.current.key === key) {
        changed = true
        setState((s) => ({ ...(s.key === key ? s : { key, items: [], exhausted: false }), error: e }))
      }
    } finally {
      inFlight.current = false
      if (!changed) setTick((t) => t + 1)
    }
  }, [key, serverId, kind, pool, order, tagId])

  const current = state.key === key ? state : { key, items: [], exhausted: false, error: null }

  // First page for new settings, then keep a few items in hand.
  useEffect(() => {
    if (!current.exhausted && !current.error && current.items.length < keep) {
      void fetchPage()
    }
  }, [current.items.length, current.exhausted, current.error, fetchPage, tick, keep])

  /** Take the first `n` items off the queue (rated or skipped). */
  const advance = useCallback(
    (n = 1) => setState((s) => (s.key === key ? { ...s, items: s.items.slice(n) } : s)),
    [key],
  )
  /** Put an item back at the front (after an undo). */
  const putBack = useCallback(
    (item: RateItem) =>
      setState((s) =>
        s.key === key ? { ...s, items: [item, ...s.items.filter((i) => i.rating_key !== item.rating_key)] } : s,
      ),
    [key],
  )
  const retry = useCallback(() => {
    setState((s) => ({ ...s, error: null }))
  }, [])

  return {
    items: current.items,
    loading: current.items.length === 0 && !current.exhausted && !current.error,
    exhausted: current.exhausted,
    error: current.error,
    advance,
    putBack,
    retry,
  }
}

export function useRate(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { rating_key: number; rating: number | null }) =>
      unwrap(api.POST('/api/servers/{server_id}/ratings', { params: { path: { server_id: serverId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: libraryKeys.library(serverId) }),
  })
}

export function useUndoRating(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/servers/{server_id}/ratings/undo', { params: { path: { server_id: serverId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: libraryKeys.library(serverId) }),
  })
}
