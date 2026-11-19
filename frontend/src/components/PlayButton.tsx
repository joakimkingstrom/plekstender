import { playSample, playTrack, sampleKey, stopAudio, unlockAudio, useAudio } from '../audio'
import type { GameMode } from '../api/game'
import { Tooltip } from './Tooltip'

/** Play / stop a 40-second preview (one plays at a time, app-wide): of a song, or with
 * `kind`, the song for a rating card (an album's or artist's popular song). */
export function PlayButton({
  serverId,
  ratingKey,
  title,
  size = 'size-8',
  kind,
}: {
  serverId: number
  ratingKey: number
  title: string
  size?: string
  kind?: GameMode
}) {
  const audio = useAudio()
  const key = kind ? sampleKey(serverId, kind, ratingKey) : `track:${serverId}:${ratingKey}`
  const mine = audio.key === key
  const loading = mine && audio.status === 'loading'
  const playing = mine && audio.status === 'playing'
  return (
    <Tooltip content={playing ? 'Stop' : kind && kind !== 'track' ? 'Play one of the most popular songs' : 'Play a preview'}>
      <button
        type="button"
        aria-label={playing ? `Stop ${title}` : `Play a preview of ${title}`}
        aria-pressed={playing}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          if (playing || loading) {
            stopAudio()
            return
          }
          unlockAudio()
          void (kind ? playSample(serverId, kind, ratingKey) : playTrack(serverId, ratingKey))
        }}
        className={`flex ${size} shrink-0 items-center justify-center rounded-full ${
          playing ? 'bg-accent text-on-accent' : 'bg-surface-2 text-text hover:bg-border'
        }`}
      >
        {loading ? (
          <span className="size-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden />
        ) : playing ? (
          <svg viewBox="0 0 12 12" className="size-3" aria-hidden>
            <rect x="2" y="2" width="8" height="8" rx="1" fill="currentColor" />
          </svg>
        ) : (
          <svg viewBox="0 0 12 12" className="ml-0.5 size-3" aria-hidden>
            <path d="M3 1.8v8.4L10 6z" fill="currentColor" />
          </svg>
        )}
      </button>
    </Tooltip>
  )
}
