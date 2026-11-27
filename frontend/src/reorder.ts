/** How long a tab is held before it can be dragged to another place (ScrollTabs). */
export const HOLD_MS = 1000

/** The order with `key` moved to `overIndex` (where it's dragged to). */
export function moved<T>(order: T[], key: T, overIndex: number): T[] {
  const from = order.indexOf(key)
  if (from === -1 || overIndex === from) return order
  const next = order.filter((k) => k !== key)
  next.splice(Math.max(0, Math.min(next.length, overIndex)), 0, key)
  return next
}
