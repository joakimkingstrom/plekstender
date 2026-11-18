import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorMessage, unwrap, type Schemas } from '../api/client'
import { keys } from '../api/hooks'

const CHANNEL = 'roadiefm-auth'
const POLL_MS = 1500
const GIVE_UP_MS = 10 * 60_000

type CheckResult = Schemas['PinCheckOut']

export type SignInState =
  | { phase: 'idle' }
  | { phase: 'waiting' }
  | { phase: 'denied'; message: string }
  | { phase: 'error'; message: string }

export function checkPin(): Promise<CheckResult> {
  return unwrap(api.POST('/api/auth/plex/check'))
}

/** Tell other tabs/windows (e.g. the one that opened the Plex popup) sign-in finished. */
export function broadcastResult(result: CheckResult) {
  try {
    const channel = new BroadcastChannel(CHANNEL)
    channel.postMessage(result)
    channel.close()
  } catch {
    // BroadcastChannel unsupported: the opener's polling still picks the result up.
  }
}

/**
 * Plex PIN sign-in.
 *
 * Desktop: opens app.plex.tv in a popup and polls the backend until the PIN is approved.
 * If the popup is blocked (common on mobile / installed PWAs) the whole page goes to
 * Plex instead and comes back to /login/callback, which finishes the sign-in.
 */
export function usePlexSignIn(onSignedIn: () => void) {
  const qc = useQueryClient()
  const [state, setState] = useState<SignInState>({ phase: 'idle' })
  const timer = useRef<number | undefined>(undefined)
  const startedAt = useRef(0)

  const stop = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = undefined
  }, [])

  const finish = useCallback(
    async (result: CheckResult) => {
      stop()
      if (result.status === 'ok' && result.user) {
        qc.setQueryData(keys.me, result.user)
        onSignedIn()
        return
      }
      if (result.status === 'denied') {
        setState({ phase: 'denied', message: result.detail ?? 'Access denied.' })
        return
      }
      // "expired" can also mean the popup's callback page already completed sign-in.
      const me = await unwrap(api.GET('/api/auth/me')).catch(() => null)
      if (me) {
        qc.setQueryData(keys.me, me)
        onSignedIn()
      } else {
        setState({ phase: 'error', message: result.detail ?? 'Sign-in timed out.' })
      }
    },
    [qc, onSignedIn, stop],
  )

  const startPolling = useCallback(() => {
    startedAt.current = Date.now()
    const tick = async () => {
      try {
        const result = await checkPin()
        if (result.status === 'pending') {
          if (Date.now() - startedAt.current > GIVE_UP_MS) {
            stop()
            setState({ phase: 'error', message: 'Sign-in timed out.' })
          } else {
            timer.current = window.setTimeout(() => void tick(), POLL_MS)
          }
          return
        }
        await finish(result)
      } catch (e) {
        stop()
        setState({ phase: 'error', message: errorMessage(e) })
      }
    }
    timer.current = window.setTimeout(() => void tick(), POLL_MS)
  }, [finish, stop])

  useEffect(() => {
    let channel: BroadcastChannel | undefined
    try {
      channel = new BroadcastChannel(CHANNEL)
      channel.onmessage = (event: MessageEvent<CheckResult>) => {
        if (timer.current !== undefined) void finish(event.data)
      }
    } catch {
      channel = undefined
    }
    return () => {
      stop()
      channel?.close()
    }
  }, [finish, stop])

  const start = useCallback(async () => {
    stop()
    setState({ phase: 'waiting' })
    // Open the popup synchronously in the click handler so it isn't blocked.
    const popup = window.open('', 'plex-auth', 'popup,width=600,height=760')
    try {
      const { auth_url } = await unwrap(api.POST('/api/auth/plex/pin'))
      if (popup && !popup.closed) {
        popup.location.href = auth_url
        startPolling()
      } else {
        window.location.assign(auth_url)
      }
    } catch (e) {
      popup?.close()
      setState({ phase: 'error', message: errorMessage(e) })
    }
  }, [startPolling, stop])

  const cancel = useCallback(() => {
    stop()
    setState({ phase: 'idle' })
  }, [stop])

  return { state, start, cancel }
}
