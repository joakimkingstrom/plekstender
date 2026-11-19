import { useEffect, useRef, useState } from 'react'
import { playClip, stopAudio, type Preview } from './audio'
import { errorMessage, type Schemas } from './api/client'
import { checkGuess, fetchRound, useRecordResult, type GameMode, type GameSearchItem } from './api/game'
import { useMe } from './api/hooks'

export type GameSettings = Schemas['GameSettings']
export const DEFAULT_GAME: GameSettings = {
  round_seconds: 30,
  max_guesses: 3,
  artist_feedback: true,
  playlist: 'Guess the intro',
  popularity: 'popular',
}

/** The account's game settings (Settings → Guess the intro), with the defaults filled in. */
export function useGameSettings(): GameSettings {
  const me = useMe()
  return { ...DEFAULT_GAME, ...me.data?.preferences?.game }
}

/** Wrong guesses (and, with feedback on, wrong artists) cost this many seconds. */
export const PENALTY = 3
/** Song and album modes: points for naming the right artist (also when the round is lost). */
export const ARTIST_BONUS = 3
/** The artist shows from this share of the round (song and album modes). */
const SHOW_ARTIST = 0.6
/** Album and artist modes: the playing song's title, as a hint, from this share. */
const SHOW_SONG = 0.67
const MAX_BLUR = 28

type Phase = 'loading' | 'playing' | 'won' | 'lost' | 'skipped' | 'error'
export type Feedback = { tone: 'good' | 'bad'; text: string } | null
type Round = {
  key: string
  phase: Phase
  preview: Preview | null
  songTitle: string | null
  startedAt: number
  penalty: number
  points: number
  guesses: number
  artistFound: boolean
  feedback: Feedback
  reason: string | null
  error: string | null
}

const fresh = (key: string): Round => ({
  key,
  phase: 'loading',
  preview: null,
  songTitle: null,
  startedAt: 0,
  penalty: 0,
  points: 0,
  guesses: 0,
  artistFound: false,
  feedback: null,
  reason: null,
  error: null,
})

export type IntroRound = ReturnType<typeof useIntroRound>
export type GuessResult = { correct: boolean; artistCorrect: boolean | null }

/**
 * One round of Guess the intro for the card on top: fetches the song to play (the card's
 * song, or a song of its album or artist), plays it, runs the clock and says what to
 * reveal. Cards that can't be played (silent intro, very short title, no file) are
 * reported through `onUnplayable` so the card can be skipped.
 */
export function useIntroRound({
  active,
  serverId,
  mode,
  ratingKey,
  nextKey,
  settings,
  onUnplayable,
}: {
  active: boolean
  serverId: number
  mode: GameMode
  ratingKey: number
  nextKey: number | null
  settings: GameSettings
  onUnplayable: (reason: string | null) => void
}) {
  const length = settings.round_seconds
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
        setRound({ ...fresh(key), phase: 'playing', preview, songTitle: r.song_title ?? null, startedAt: started })
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
      ? Math.min(length, (now - current.startedAt) / 1000 + current.penalty)
      : current.phase === 'loading'
        ? 0
        : length
  const bonus = (found: boolean) => (mode !== 'artist' && found ? ARTIST_BONUS : 0)

  const finish = (won: boolean, points: number, feedback: Feedback = null) => {
    const streak = won ? session.streak + 1 : 0
    setSession((s) => ({ score: s.score + points, streak }))
    setRound((r) => (r.key === key ? { ...r, phase: won ? 'won' : 'lost', points, feedback: feedback ?? r.feedback } : r))
    record.mutate({ mode, correct: won, points, streak })
  }

  // The clock: ticks while playing, and ends the round when time is up.
  const finishRef = useRef(finish)
  useEffect(() => {
    finishRef.current = finish
  })
  const artistFoundRef = useRef(false)
  useEffect(() => {
    artistFoundRef.current = current.artistFound
  })
  useEffect(() => {
    if (current.phase !== 'playing') return
    const timer = window.setInterval(() => {
      const t = performance.now()
      setNow(t)
      if ((t - current.startedAt) / 1000 + current.penalty >= length)
        finishRef.current(false, bonus(artistFoundRef.current))
    }, 100)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- bonus only depends on mode
  }, [current.phase, current.startedAt, current.penalty, length])

  const update = (patch: Partial<Round>) => setRound((r) => (r.key === key ? { ...r, ...patch } : r))
  const feedbackOn = settings.artist_feedback

  /**
   * Check a guess. Picking an artist while guessing a song is a step on the way (it doesn't
   * use up a guess); anything else is an answer, and a wrong one costs PENALTY seconds.
   */
  const guess = async (item: GameSearchItem): Promise<GuessResult> => {
    if (current.phase !== 'playing') return { correct: false, artistCorrect: null }
    const result = await checkGuess(serverId, mode, ratingKey, item.rating_key, item.kind)
    const artistCorrect = result.artist_correct ?? null
    const found = current.artistFound || artistCorrect === true

    if (item.kind === 'artist' && mode !== 'artist') {
      if (artistCorrect) {
        update({ artistFound: true, feedback: feedbackOn ? { tone: 'good', text: `Right artist! +${ARTIST_BONUS}` } : null })
      } else if (feedbackOn) {
        update({ penalty: current.penalty + PENALTY, feedback: { tone: 'bad', text: `Not by ${item.title}: −${PENALTY} s` } })
      }
      return { correct: false, artistCorrect }
    }

    if (result.correct) {
      finish(true, Math.max(1, Math.ceil(length - elapsed)) + bonus(true), { tone: 'good', text: 'Correct!' })
      return { correct: true, artistCorrect: true }
    }
    const guesses = current.guesses + 1
    const wrong =
      mode === 'artist' || !feedbackOn
        ? `Not “${item.title}”`
        : artistCorrect
          ? `Right artist${current.artistFound ? '' : ` (+${ARTIST_BONUS})`}, but not “${item.title}”`
          : `Not “${item.title}”, and not the artist either`
    const out = settings.max_guesses > 0 && guesses >= settings.max_guesses
    const feedback: Feedback = { tone: mode !== 'artist' && feedbackOn && artistCorrect ? 'good' : 'bad', text: out ? `${wrong}. No guesses left.` : `${wrong}: −${PENALTY} s` }
    if (out) {
      update({ guesses, artistFound: found })
      finish(false, bonus(found), feedback)
    } else {
      update({ guesses, artistFound: found, penalty: current.penalty + PENALTY, feedback })
    }
    return { correct: false, artistCorrect }
  }

  const playing = current.phase === 'playing' || current.phase === 'loading'
  const guessesLeft = settings.max_guesses > 0 ? Math.max(0, settings.max_guesses - current.guesses) : null
  return {
    phase: current.phase,
    playing,
    length,
    elapsed,
    points: current.points,
    reason: current.reason,
    error: current.error,
    feedback: current.feedback,
    guessesLeft,
    artistFound: current.artistFound,
    session,
    guess,
    giveUp: () => current.phase === 'playing' && finish(false, bonus(current.artistFound)),
    /** What the card may show right now. The answer's title only once the round is over. */
    reveal: {
      blur: current.phase === 'loading' ? MAX_BLUR : playing ? MAX_BLUR * (1 - elapsed / length) : 0,
      artist: !playing || (mode !== 'artist' && elapsed >= length * SHOW_ARTIST),
      title: !playing,
      /** Album and artist modes: the song playing, late in the round (and afterwards). */
      song: mode !== 'track' && (!playing || elapsed >= length * SHOW_SONG) ? current.songTitle : null,
    },
  }
}
