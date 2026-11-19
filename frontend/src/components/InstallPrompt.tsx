import { useState, useSyncExternalStore, type ReactNode } from 'react'
import { install, installOffer, isPhone, isStandalone, onInstallOffer, platform } from '../install'
import { Mark } from './Layout'
import { Dialog } from './Dialog'
import { Button } from './ui'

const SEEN_KEY = 'plekstender.install-prompt'

function seen(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) !== null
  } catch {
    return true // no storage: don't nag on every visit
  }
}

/**
 * Once, on a phone: suggest adding Plekstender to the home screen, with the steps for the
 * phone in hand (iOS Safari or Android Chrome). Not shown inside the installed app.
 */
export function InstallPrompt() {
  const [open, setOpen] = useState(() => isPhone() && !isStandalone() && !seen())
  const offer = useSyncExternalStore(onInstallOffer, installOffer, () => null)
  if (!open) return null

  const close = () => {
    try {
      localStorage.setItem(SEEN_KEY, new Date().toISOString())
    } catch {
      // storage unavailable
    }
    setOpen(false)
  }
  const os = platform()

  return (
    <Dialog title="Put Plekstender on your home screen" onClose={close}>
      <div className="space-y-4 text-sm">
        <div className="flex items-center gap-3">
          <Mark className="size-10 shrink-0 text-text" />
          <p className="text-muted">
            It then opens full screen like an app, straight into rating, without the browser's address bar.
          </p>
        </div>
        {os === 'android' && offer ? (
          <Button
            variant="primary"
            className="w-full"
            onClick={() => {
              void install().then(close)
            }}
          >
            Install Plekstender
          </Button>
        ) : null}
        {os === 'ios' ? (
          <ol className="space-y-3">
            <Step n={1}>
              In Safari, tap the <strong>Share</strong> button <ShareIcon /> in the toolbar (at the bottom, or at the top
              on an iPad).
            </Step>
            <Step n={2}>
              Scroll down and tap <strong>Add to Home Screen</strong>.
            </Step>
            <Step n={3}>
              Tap <strong>Add</strong>, then open Plekstender from its new icon.
            </Step>
          </ol>
        ) : os === 'android' ? (
          <ol className="space-y-3">
            {offer && <p className="text-xs text-muted">Or by hand:</p>}
            <Step n={1}>
              In Chrome, tap the menu <strong>⋮</strong> (top right).
            </Step>
            <Step n={2}>
              Tap <strong>Add to Home screen</strong> (or <strong>Install app</strong>).
            </Step>
            <Step n={3}>
              Tap <strong>Install</strong> or <strong>Add</strong>, then open Plekstender from its new icon.
            </Step>
          </ol>
        ) : (
          <p>
            In your browser's menu, look for <strong>Add to Home Screen</strong> or <strong>Install app</strong>.
          </p>
        )}
        <p className="text-xs text-muted">The steps are also in Settings → Appearance.</p>
        <Button variant="secondary" className="w-full" onClick={close}>
          {os === 'other' ? 'OK' : 'Got it'}
        </Button>
      </div>
    </Dialog>
  )
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
        {n}
      </span>
      <span>{children}</span>
    </li>
  )
}

/** The iOS share symbol: a box with an arrow going up. */
function ShareIcon() {
  return (
    <svg viewBox="0 0 16 16" className="inline size-4 align-text-bottom text-accent" aria-label="Share" role="img">
      <path d="M8 1.5v8M5 4.2 8 1.5l3 2.7" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5.5 6.5H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1h-1.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}
