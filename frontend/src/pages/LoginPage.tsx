import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { api, errorMessage, unwrap } from '../api/client'
import { useMe } from '../api/hooks'
import { broadcastResult, checkPin, usePlexSignIn } from '../auth/plexSignIn'
import { Wordmark } from '../components/Layout'
import { Button, Notice, Spinner } from '../components/ui'
import { useTheme } from '../theme'

function safeRedirect(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from
  return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/'
}

function LoginShell({ children }: { children: React.ReactNode }) {
  useTheme()
  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-surface p-8 text-center">
        <div className="mb-2 flex justify-center">
          <Wordmark size="text-3xl" />
        </div>
        <p className="mb-8 text-sm text-muted">Rate, tidy and curate your Plex music library.</p>
        {children}
      </div>
    </div>
  )
}

export function LoginPage() {
  const { data: me } = useMe()
  const navigate = useNavigate()
  const location = useLocation()
  const target = safeRedirect(location.state)
  const onSignedIn = useCallback(() => navigate(target, { replace: true }), [navigate, target])
  const { state, start, cancel } = usePlexSignIn(onSignedIn)
  // A new instance: whoever signs in first becomes its admin, with the setup code from the log.
  const setup = useQuery({ queryKey: ['auth', 'setup'], queryFn: () => unwrap(api.GET('/api/auth/setup')) })
  const unclaimed = setup.data?.claimed === false
  const [code, setCode] = useState('')
  const codeId = useId()

  if (me) return <Navigate to={target} replace />

  return (
    <LoginShell>
      {state.phase === 'waiting' ? (
        <div className="space-y-4">
          <div className="flex items-center justify-center gap-3 text-sm text-muted">
            <Spinner small /> Waiting for Plex…
          </div>
          <p className="text-xs text-muted">Finish signing in in the Plex window.</p>
          <Button variant="ghost" onClick={cancel}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {state.phase === 'denied' && <Notice tone="warn">{state.message}</Notice>}
          {state.phase === 'error' && <Notice tone="error">{state.message}</Notice>}
          {unclaimed && (
            <div className="space-y-2 text-left text-sm">
              <p className="text-muted">
                This Plekstender hasn’t been set up yet. Whoever signs in first becomes its admin, with the setup code from
                the web app’s log: run <code className="rounded bg-surface-2 px-1">docker compose logs frontend</code>, or
                open the frontend container’s log in your Docker tool.
              </p>
              <label htmlFor={codeId} className="block font-medium">
                Setup code
              </label>
              <input
                id={codeId}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="XXXX-XXXX-XXXX"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono tracking-wider"
              />
            </div>
          )}
          <Button
            variant="primary"
            size="lg"
            className="w-full"
            disabled={unclaimed && !code.trim()}
            onClick={() => void start(unclaimed ? code.trim() : undefined)}
          >
            Sign in with Plex
          </Button>
        </div>
      )}
    </LoginShell>
  )
}

/** Plex sends the browser here after the user approves the sign-in. */
export function LoginCallbackPage() {
  const navigate = useNavigate()
  const [message, setMessage] = useState<{ tone: 'warn' | 'error'; text: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    async function finish(attempt: number) {
      try {
        const result = await checkPin()
        if (cancelled) return
        if (result.status === 'pending' && attempt < 8) {
          window.setTimeout(() => void finish(attempt + 1), 1000)
          return
        }
        broadcastResult(result)
        if (result.status === 'ok') {
          if (window.opener) {
            window.close()
          }
          navigate('/', { replace: true })
        } else if (result.status === 'denied') {
          setMessage({ tone: 'warn', text: result.detail ?? 'Access denied.' })
        } else if (window.opener) {
          // The opener window may already have finished signing in.
          window.close()
        } else {
          navigate('/login', { replace: true })
        }
      } catch (e) {
        if (!cancelled) setMessage({ tone: 'error', text: errorMessage(e) })
      }
    }
    void finish(0)
    return () => {
      cancelled = true
    }
  }, [navigate])

  return (
    <LoginShell>
      {message ? (
        <div className="space-y-4">
          <Notice tone={message.tone}>{message.text}</Notice>
          <Button onClick={() => navigate('/login', { replace: true })}>Back</Button>
        </div>
      ) : (
        <div className="flex items-center justify-center gap-3 text-sm text-muted">
          <Spinner small /> Signing you in…
        </div>
      )}
    </LoginShell>
  )
}
