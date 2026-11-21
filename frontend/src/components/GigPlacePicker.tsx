import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useGigPlaceSearch, type Gig, type GigPlaceFound, type PlaceSearch } from '../api/gigs'
import { Button, Notice, Spinner } from './ui'

const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'

export type PlaceChoice = { picked: GigPlaceFound | null; findAgain: boolean }

/**
 * A gig's place on the map: what's known (found, only the town, picked by hand), and a
 * search on OpenStreetMap to pick it by hand.
 */
export function GigPlacePicker({
  gig,
  venue,
  city,
  country,
  choice,
  onChange,
}: {
  gig?: Gig
  venue: string
  city: string
  country: string
  choice: PlaceChoice
  onChange: (choice: PlaceChoice) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [search, setSearch] = useState<PlaceSearch | null>(null)
  const results = useGigPlaceSearch(search)
  // Changing the venue, town or country looks the place up again (unless one is picked).
  const moved =
    gig !== undefined &&
    ((gig.venue ?? '') !== venue.trim() || (gig.city ?? '') !== city.trim() || (gig.country ?? '') !== country.trim())
  const status = choice.picked
    ? `Picked: ${choice.picked.name}${choice.picked.address ? `, ${choice.picked.address}` : ''}.`
    : choice.findAgain
      ? 'Looked up again from the venue and town when you save.'
      : gig && moved
        ? 'Looked up again from the new venue and town when you save.'
        : gig?.place_precision === 'manual'
          ? 'Picked by you.'
          : gig?.place_precision === 'venue'
            ? 'Found on the map from the venue.'
            : gig?.place_precision === 'city'
              ? `The venue wasn’t found, so it’s shown at ${gig.city ?? 'the town'}.`
              : gig && gig.latitude == null && (gig.venue || gig.city)
                ? 'Not found on the map yet.'
                : 'Looked up from the venue and town when you save.'
  const begin = () => {
    const text = [venue.trim(), city.trim()].filter(Boolean).join(', ')
    setQ(text)
    setOpen(true)
    if (text.length >= 2) setSearch({ q: text, country: country.trim() || null })
  }
  const offerFindAgain = gig?.place_precision === 'manual' && !choice.findAgain && !choice.picked && !moved

  return (
    <div className="space-y-2 text-sm sm:col-span-2">
      <p className="font-medium">Place on the map</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p className={gig?.place_precision === 'city' && !choice.picked ? 'text-warn' : 'text-muted'}>{status}</p>
        {!open && (
          <button type="button" className="text-accent hover:underline" onClick={begin}>
            {choice.picked || gig?.place_precision === 'manual' ? 'Pick another place' : 'Pick it yourself'}
          </button>
        )}
        {choice.picked && (
          <button type="button" className="text-muted hover:text-text hover:underline" onClick={() => onChange({ picked: null, findAgain: false })}>
            Undo
          </button>
        )}
        {offerFindAgain && (
          <button type="button" className="text-muted hover:text-text hover:underline" onClick={() => onChange({ picked: null, findAgain: true })}>
            Look it up again
          </button>
        )}
      </div>
      {open && (
        <div className="space-y-2 rounded-lg border border-border p-3">
          <div className="flex gap-2" role="search">
            <input
              aria-label="Search for the place"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  if (q.trim().length >= 2) setSearch({ q: q.trim(), country: country.trim() || null })
                }
              }}
              placeholder="Venue, town"
              className={field}
            />
            <Button
              variant="secondary"
              disabled={q.trim().length < 2}
              onClick={() => setSearch({ q: q.trim(), country: country.trim() || null })}
              className="shrink-0"
            >
              Search
            </Button>
          </div>
          {search === null ? null : results.isFetching ? (
            <Spinner small />
          ) : results.isError ? (
            <Notice tone="error">{errorMessage(results.error)}</Notice>
          ) : results.data && results.data.length === 0 ? (
            <p className="text-muted">Nothing found. Try the venue’s name with the town, or the street address.</p>
          ) : (
            <ul className="max-h-64 space-y-0.5 overflow-y-auto" aria-label="Places found">
              {results.data?.map((p) => (
                <li key={`${p.latitude},${p.longitude},${p.name}`}>
                  <button
                    type="button"
                    className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-surface-2"
                    onClick={() => {
                      onChange({ picked: p, findAgain: false })
                      setOpen(false)
                    }}
                  >
                    <span className="block font-medium">{p.name}</span>
                    <span className="block text-xs text-muted">{[p.address, p.kind.replace(/_/g, ' ')].filter(Boolean).join(' · ')}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted">From OpenStreetMap. Your other gigs at this venue get the same place.</p>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
