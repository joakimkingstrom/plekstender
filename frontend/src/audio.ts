import { useSyncExternalStore } from 'react'
import { api, unwrap, type Schemas } from './api/client'

export type Preview = Schemas['PreviewOut']
type State = { key: string | null; status: 'idle' | 'loading' | 'playing' | 'error'; error: string | null }

/**
 * One audio element for the whole app, so only one preview plays at a time. iOS only lets
 * a page play sound after a tap: the first `unlock()` (from a tap) makes later plays work.
 */
let audio: HTMLAudioElement | null = null
let state: State = { key: null, status: 'idle', error: null }
let stopAt: number | null = null
const listeners = new Set<() => void>()

function set(next: Partial<State>) {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

function element(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio()
    audio.preload = 'auto'
    audio.addEventListener('ended', () => set({ status: 'idle', key: null }))
    audio.addEventListener('pause', () => {
      if (state.status === 'playing') set({ status: 'idle', key: null })
    })
    audio.addEventListener('timeupdate', () => {
      if (stopAt !== null && audio && audio.currentTime >= stopAt) audio.pause()
    })
  }
  return audio
}

/** 0.1 s of silence as a WAV data URL (8 kHz, 8-bit mono). */
function silentWav(): string {
  const samples = 800
  const bytes = new Uint8Array(44 + samples)
  const view = new DataView(bytes.buffer)
  const text = (offset: number, value: string) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)))
  text(0, 'RIFF')
  view.setUint32(4, 36 + samples, true)
  text(8, 'WAVEfmt ')
  view.setUint32(16, 16, true) // fmt chunk size
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, 8000, true) // sample rate
  view.setUint32(28, 8000, true) // byte rate
  view.setUint16(32, 1, true) // block align
  view.setUint16(34, 8, true) // bits per sample
  text(36, 'data')
  view.setUint32(40, samples, true)
  bytes.fill(128, 44) // 8-bit silence
  let binary = ''
  bytes.forEach((b) => (binary += String.fromCharCode(b)))
  return `data:audio/wav;base64,${btoa(binary)}`
}

/** Call from a tap before playing later without one (iOS). */
export function unlockAudio() {
  const el = element()
  if (el.src) return
  // A moment of silence, played and paused at once.
  el.src = silentWav()
  void el.play().then(
    () => el.pause(),
    () => undefined,
  )
}

export function previewInfo(serverId: number, ratingKey: number): Promise<Preview> {
  return unwrap(
    api.GET('/api/servers/{server_id}/tracks/{rating_key}/preview', {
      params: { path: { server_id: serverId, rating_key: ratingKey } },
    }),
  )
}

/** Play a prepared clip from where its music starts, for `seconds` (default: the clip).
 * The start goes in the URL (`#t=`), and play() is called straight away: iOS doesn't load
 * media until it's asked to play, so waiting for metadata first would never finish. */
export async function playClip(key: string, preview: Preview, seconds?: number): Promise<void> {
  const el = element()
  set({ key, status: 'loading', error: null })
  const start = preview.start_ms / 1000
  el.src = start > 0 ? `${preview.url}#t=${start.toFixed(2)}` : preview.url
  stopAt = seconds ? start + seconds : null
  try {
    await el.play()
    if (state.key !== key) return // something else started meanwhile
    set({ status: 'playing' })
  } catch (e) {
    if (state.key === key) set({ status: 'error', error: e instanceof Error ? e.message : 'Playback failed.', key: null })
    throw e
  }
}

/** Fetch a song's preview and play it. */
export async function playTrack(serverId: number, ratingKey: number): Promise<void> {
  const key = `track:${serverId}:${ratingKey}`
  set({ key, status: 'loading', error: null })
  try {
    const preview = await previewInfo(serverId, ratingKey)
    if (state.key !== key) return
    await playClip(key, preview)
  } catch (e) {
    if (state.key === key) set({ status: 'error', error: e instanceof Error ? e.message : 'Playback failed.', key: null })
  }
}

export function stopAudio() {
  audio?.pause()
  set({ key: null, status: 'idle' })
}

/** Seconds into the current clip (from where its music started), or 0. */
export function audioElapsed(preview: Preview | null): number {
  if (!audio || !preview) return 0
  return Math.max(0, audio.currentTime - preview.start_ms / 1000)
}

export function useAudio(): State {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
    () => state,
  )
}
