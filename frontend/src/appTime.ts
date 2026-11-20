import { useEffect } from 'react'
import { api } from './api/client'
import { localTimeZone } from './api/stats'

const MINUTE = 60_000
const IDLE_AFTER = 2 * MINUTE

/** Reports time spent in Plekstender (for Rewind): each minute the page is visible and was
 * used in the last two minutes counts. Nothing else is sent. */
export function useAppTime(signedIn: boolean) {
  useEffect(() => {
    if (!signedIn) return
    let lastUsed = Date.now()
    const used = () => {
      lastUsed = Date.now()
    }
    const events = ['pointerdown', 'keydown', 'scroll', 'touchstart'] as const
    for (const e of events) window.addEventListener(e, used, { passive: true })
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || Date.now() - lastUsed > IDLE_AFTER) return
      void api
        .POST('/api/app-time', { params: { query: { tz: localTimeZone() } }, body: { seconds: 60 } })
        .catch(() => undefined) // offline: that minute isn't counted
    }, MINUTE)
    return () => {
      window.clearInterval(timer)
      for (const e of events) window.removeEventListener(e, used)
    }
  }, [signedIn])
}
