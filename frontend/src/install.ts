/** Installing Plekstender as an app (a PWA) on a phone. */

type InstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

let deferred: InstallPromptEvent | null = null
const listeners = new Set<() => void>()

// Chrome on Android offers its own install dialog; keep the offer for an "Install" button.
// (It's fired early, so listen as soon as the app loads.)
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferred = e as InstallPromptEvent
    listeners.forEach((l) => l())
  })
  window.addEventListener('appinstalled', () => {
    deferred = null
    listeners.forEach((l) => l())
  })
}

export function installOffer(): InstallPromptEvent | null {
  return deferred
}

export function onInstallOffer(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export async function install(): Promise<boolean> {
  if (!deferred) return false
  await deferred.prompt()
  const { outcome } = await deferred.userChoice
  deferred = null
  listeners.forEach((l) => l())
  return outcome === 'accepted'
}

/** Already running as an installed app (from the home screen). */
export function isStandalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean }
  return Boolean(nav.standalone) || Boolean(window.matchMedia?.('(display-mode: standalone)').matches)
}

export type Platform = 'ios' | 'android' | 'other'

export function platform(ua = navigator.userAgent, touchPoints = navigator.maxTouchPoints ?? 0): Platform {
  // iPadOS reports itself as a Mac, but with touch.
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1)) return 'ios'
  if (/Android/i.test(ua)) return 'android'
  return 'other'
}

export function isPhone(): boolean {
  return Boolean(window.matchMedia?.('(max-width: 767px)').matches)
}
