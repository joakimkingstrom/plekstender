import { useState } from 'react'
import { useHealth } from '../api/hooks'
import { BUILD_VERSION, isStaging, wasUpdated } from '../version'
import { Button } from './ui'

/**
 * Plekstender was updated while this page was open (a tab or the installed app left open):
 * offer a reload, so the page matches the server again. A staging build (a version ending
 * in "-staging") always says so here, without the buttons once the page is up to date.
 */
export function UpdateBanner() {
  const health = useHealth()
  const [dismissed, setDismissed] = useState<string | null>(null)
  const server = health.data?.version
  const staging = isStaging(BUILD_VERSION) || isStaging(server)
  if (wasUpdated(BUILD_VERSION, server) && dismissed !== server)
    return (
      <div role="status" className={BAR}>
        <span>
          Plekstender has been updated to {server}
          {isStaging(server) ? ' (a staging build)' : ''}.
        </span>
        <span className="flex gap-2">
          <Button size="xs" variant="primary" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setDismissed(server ?? null)}>
            Later
          </Button>
        </span>
      </div>
    )
  if (!staging) return null
  return (
    <div role="status" className={BAR}>
      <span>
        You're running the staging version{BUILD_VERSION ? ` (${BUILD_VERSION})` : ''}: changes still being tried out
        before they go into the main version.
      </span>
    </div>
  )
}

const BAR =
  'flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-accent/30 bg-accent/10 px-4 py-2 text-center text-xs'
