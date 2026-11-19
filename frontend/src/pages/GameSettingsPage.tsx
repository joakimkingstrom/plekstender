import { useState, type ReactNode } from 'react'
import { useGamePlaylists } from '../api/game'
import { errorMessage } from '../api/client'
import { useMe, useSetPreferences } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { Segmented } from '../components/Segmented'
import { Select } from '../components/Select'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { DEFAULT_GAME, type GameSettings } from '../game'

const LENGTHS: GameSettings['round_seconds'][] = [15, 20, 25, 30]
const GUESSES = [1, 2, 3, 5, 10, 0]

/** Settings for Guess the intro (per account). */
export function GameSettingsPage() {
  const me = useMe()
  if (me.isPending) return <Spinner />
  return <GameSettingsForm initial={{ ...DEFAULT_GAME, ...me.data?.preferences?.game }} />
}

function GameSettingsForm({ initial }: { initial: GameSettings }) {
  const save = useSetPreferences()
  const { active } = useActiveServer()
  const playlists = useGamePlaylists(active?.id ?? null)
  const [form, setForm] = useState<GameSettings>(initial)
  const [saved, setSaved] = useState(false)
  const set = (patch: Partial<GameSettings>) => {
    setSaved(false)
    setForm((f) => ({ ...f, ...patch }))
  }
  const changed = JSON.stringify(form) !== JSON.stringify(initial)
  const valid = form.playlist.trim().length > 0

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Rounds">
        <div className="space-y-5 text-sm">
          <Row label="Round length" note="How long you have to guess. The artwork sharpens over the round.">
            <Segmented
              label="Round length"
              value={String(form.round_seconds)}
              options={LENGTHS.map((n) => [String(n), `${n} s`])}
              onChange={(v) => set({ round_seconds: Number(v) as GameSettings['round_seconds'] })}
            />
          </Row>
          <Row label="Guesses per round" note="A wrong guess also costs 3 seconds.">
            <Select
              label="Guesses per round"
              value={form.max_guesses}
              onChange={(max_guesses) => set({ max_guesses })}
              options={GUESSES.map((n) => ({ value: n, label: n === 0 ? 'As many as time allows' : String(n) }))}
            />
          </Row>
          <Row
            label="Right artist?"
            note="When guessing songs or albums: say whether a guess (or the artist you pick) is by the right artist. Naming the right artist earns 3 points either way."
          >
            <Segmented
              label="Right artist feedback"
              value={form.artist_feedback ? 'on' : 'off'}
              options={[
                ['on', 'Tell me'],
                ['off', "Don't tell me"],
              ]}
              onChange={(v) => set({ artist_feedback: v === 'on' })}
            />
          </Row>
          <Row label="Songs" note="Which songs come up: the cards' order, and which songs play for albums and artists.">
            <Segmented
              label="Popularity"
              value={form.popularity}
              options={[
                ['popular', 'Well-known'],
                ['mixed', 'Mixed'],
                ['deep', 'Deep cuts'],
              ]}
              onChange={(popularity) => set({ popularity })}
            />
          </Row>
        </div>
      </Card>
      <Card title="Playlist">
        <div className="space-y-3 text-sm">
          <p className="text-muted">
            After a round, a button adds the artist's five most popular songs to this playlist in Plex. It's created
            the first time if it doesn't exist.
          </p>
          <label className="block">
            <span className="mb-1 block font-medium">Playlist name</span>
            <input
              value={form.playlist}
              maxLength={100}
              list="game-playlists"
              onChange={(e) => set({ playlist: e.target.value })}
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
            />
            <datalist id="game-playlists">
              {(playlists.data ?? []).map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          {playlists.data && playlists.data.length > 0 && (
            <p className="text-xs text-muted">Pick one of your playlists, or type a new name.</p>
          )}
        </div>
      </Card>
      <div className="flex items-center gap-3 lg:col-span-2">
        <Button
          variant="primary"
          disabled={!changed || !valid}
          busy={save.isPending}
          onClick={() => save.mutate({ game: { ...form, playlist: form.playlist.trim() } }, { onSuccess: () => setSaved(true) })}
        >
          Save
        </Button>
        <Button variant="ghost" disabled={JSON.stringify(form) === JSON.stringify(DEFAULT_GAME)} onClick={() => set(DEFAULT_GAME)}>
          Defaults
        </Button>
        {saved && !changed && <span className="text-sm text-ok">Saved.</span>}
      </div>
      {save.isError && (
        <div className="lg:col-span-2">
          <Notice tone="error">{errorMessage(save.error)}</Notice>
        </div>
      )}
    </div>
  )
}

function Row({ label, note, children }: { label: string; note: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{label}</span>
        {children}
      </div>
      <p className="text-xs text-muted">{note}</p>
    </div>
  )
}
