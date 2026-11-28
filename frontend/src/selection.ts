import { useCallback, useRef, useState } from 'react'

/**
 * Ticking items in a list: a click toggles one; a shift-click sets every item from the last
 * one clicked to this one (in the list's order) the way this one is set.
 */
export function useRangeSelection<K>() {
  const [selected, setSelected] = useState<Set<K>>(() => new Set())
  const last = useRef<K | null>(null)
  const toggle = useCallback((key: K, order: K[], shift: boolean) => {
    const anchor = last.current // read now: the update below runs later
    last.current = key
    setSelected((current) => {
      const next = new Set(current)
      const on = !current.has(key)
      const from = shift && anchor !== null ? order.indexOf(anchor) : -1
      const to = order.indexOf(key)
      if (from >= 0 && to >= 0) {
        for (const k of order.slice(Math.min(from, to), Math.max(from, to) + 1)) {
          if (on) next.add(k)
          else next.delete(k)
        }
      } else if (on) next.add(key)
      else next.delete(key)
      return next
    })
  }, [])
  const clear = useCallback(() => {
    setSelected(new Set())
    last.current = null
  }, [])
  return { selected, toggle, clear }
}
