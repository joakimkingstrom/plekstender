import { useEffect, useRef, useState } from 'react'
import { playClip, stopAudio, type Preview } from './audio'
import { errorMessage } from './api/client'
import { checkGuess, fetchRound, useRecordResult, type GameMode, type GameSearchItem } from './api/game'

/** Seconds per round. */
export const ROUND = 30
/** The artist is shown from here (not in artist mode, where it's the answer). */
export const SHOW_ARTIST = 18
/** And the song's or album's title from here. */
export const SHOW_TITLE = 26
export const PENALTY = 3
const MAX_BLUR = 28

type Phase = 'loading' | 'playing' | 'won' | 'lost' | 'skipped' | 'error'
type Round = {
  key: string
  phase: Phase
  preview: Preview | null
  startedAt: number
  penalty: number
  points: number
  reason: string | null
  error: string | null
}

const fresh = (key: string): Round => ({
  key,
  phase: 'loading',
  preview: null,
  startedAt: 0,
  penalty: 0,
  points: 0,
  reason: null,
  error: null,
})

export type IntroRound = ReturnType<typeof useIntroRound>

/**
 * One round of Guess the intro for the card on top: fetches the song to play (the card's
 * song, or a popular song of its album or artist), plays it, runs the 30-second clock and
 * says what to reveal. Cards that can't be played (silent intro, very short title, no file)
 * are reported through `onUnplayable` so the card can be skipped.
 */
export function useIntroRound({
  active,
  serverId,
  mode,
  ratingKey,
  nextKey,
  onUnplayable,
}: {
  active: boolean
  serverId: number
  mode: GameMode
  ratingKey: number
  nextKey: number | null
  onUnplayable: (reason: string | null) => void
}) {
  const key = `${serverId}:${mode}:${ratingKey}`
  const [round, setRound] = useState<Round>(() => fresh(''))
  const [now, setNow] = useState(0)
  const [session, setSession] = useState({ score: 0, streak: 0 })
  const record = useRecordResult()
  const current = round.key === key ? round : fresh(key)
  const unplayable = useRef(onUnplayable)
  useEffect(() => {
    unplayable.current = onUnplayable
  })

  // Start the round when the card comes up (and prepare the next card's).
  useEffect(() => {
    if (!active) return
    let cancelled = false
    fetchRound(serverId, mode, ratingKey)
      .then(async (r) => {
        if (cancelled) return
        if (!r.playable || !r.preview) {
          setRound({ ...fresh(key), phase: 'skipped', reason: r.reason ?? null })
          unplayable.current(r.reason ?? null)
          return
        }
        const preview = r.preview
        await playClip(`game:${key}`, preview)
        if (cancelled) return
        const started = performance.now()
        setNow(started)
        setRound({ ...fresh(key), phase: 'playing', preview, startedAt: started })
      })
      .catch((e: unknown) => {
        if (!cancelled) setRound({ ...fresh(key), phase: 'error', error: errorMessage(e) })
      })
    if (nextKey !== null) void fetchRound(serverId, mode, nextKey).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [active, key, serverId, mode, ratingKey, nextKey])

  // Leaving the game stops the music.
  useEffect(() => {
    if (!active) return
    return () => stopAudio()
  }, [active])

  // The clock starts when the music does: nothing is revealed while the song loads.
  const elapsed =
    current.phase === 'playing'
      ? Math.min(ROUND, (now - current.startedAt) / 1000 + current.penalty)
      : current.phase === 'loading'
        ? 0
        : ROUND

  const finish = (won: boolean, points: number) => {
    const streak = won ? session.streak + 1 : 0
    setSession((s) => ({ score: s.score + (won ? points : 0), streak }))
    setRound((r) => (r.key === key ? { ...r, phase: won ? 'won' : 'lost', points } : r))
    record.mutate({ mode, correct: won, points: won ? points : 0, streak })
  }

  // The clock: ticks while playing, and ends the round at 30 seconds.
  const finishRef = useRef(finish)
  useEffect(() => {
    finishRef.current = finish
  })
  useEffect(() => {
    if (current.phase !== 'playing') return
    const timer = window.setInterval(() => {
      const t = performance.now()
      setNow(t)
      if ((t - current.startedAt) / 1000 + current.penalty >= ROUND) finishRef.current(false, 0)
    }, 100)
    return () => window.clearInterval(timer)
  }, [current.phase, current.startedAt, current.penalty])

  /** Check a guess; a wrong one costs PENALTY seconds. Returns whether it was right. */
  const guess = async (item: GameSearchItem): Promise<boolean> => {
    if (current.phase !== 'playing') return false
    const { correct } = await checkGuess(serverId, mode, ratingKey, item.rating_key)
    if (correct) {
      finish(true, Math.max(1, Math.ceil(ROUND - elapsed)))
    } else {
      setRound((r) => (r.key === key ? { ...r, penalty: r.penalty + PENALTY } : r))
    }
    return correct
  }

  const playing = current.phase === 'playing' || current.phase === 'loading'
  return {
    phase: current.phase,
    playing,
    elapsed,
    points: current.points,
    reason: current.reason,
    error: current.error,
    session,
    guess,
    giveUp: () => current.phase === 'playing' && finish(false, 0),
    /** What the card may show right now. */
    reveal: {
      blur: current.phase === 'loading' ? MAX_BLUR : playing ? MAX_BLUR * (1 - elapsed / ROUND) : 0,
      artist: !playing || (mode !== 'artist' && elapsed >= SHOW_ARTIST),
      title: !playing || (mode !== 'artist' && elapsed >= SHOW_TITLE),
    },
  }
}
