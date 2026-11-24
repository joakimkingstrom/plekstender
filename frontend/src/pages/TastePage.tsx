import { errorMessage } from '../api/client'
import { useActiveServer } from '../api/library'
import { useTaste } from '../api/stats'
import { ColumnChart, ShareGrid } from '../components/charts'
import { Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatShare } from '../format'

function years(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'year' : 'years'}`
}

/** History → Taste: how your genres and the age of your music changed, year by year. */
export function TastePage() {
  const { active } = useActiveServer()
  const serverId = active && active.music_section_keys.length > 0 ? active.id : null
  const taste = useTaste(serverId)

  if (serverId === null) return <Notice tone="warn">Choose a music library first: genres and release years come from it.</Notice>
  if (taste.isPending) return <Spinner />
  if (taste.isError) return <Notice tone="error">{errorMessage(taste.error)}</Notice>
  const t = taste.data
  if (t.years.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted">No listens yet. Once you've some history, this shows how your taste changes from year to year.</p>
      </Card>
    )
  }

  const columns = t.years.map(String)
  const totalListens = t.listens.reduce((a, b) => a + b, 0)
  const withGenre = t.genre_matched.reduce((a, b) => a + b, 0)
  const withYear = t.decade_matched.reduce((a, b) => a + b, 0)
  const ages = t.years
    .map((year, i) => ({ year, median: t.median_release[i] }))
    .filter((a): a is { year: number; median: number } => a.median !== null)
  const latest = ages.at(-1)

  return (
    <div className="space-y-4">
      <Card title="Your genres over the years">
        {t.genres.length === 0 ? (
          <p className="text-sm text-muted">None of the artists you've played has a genre in Plex.</p>
        ) : (
          <>
            <ShareGrid
              rows={t.genres}
              columns={columns}
              rowHeader="Genre"
              caption="Share of each year's listens by genre"
              note={(c) => `of ${formatCount(t.genre_matched[c])} listens with a genre`}
            />
            <p className="mt-3 text-xs text-muted">
              Your {t.genres.length} most played genres. Each listen counts towards its artist's genres in Plex (split
              when an artist has several). Based on {formatShare(withGenre, totalListens)} of your listens: the rest are
              by artists not in this library, or without a genre.
            </p>
          </>
        )}
      </Card>

      <Card title="When your music is from">
        {t.decades.length === 0 ? (
          <p className="text-sm text-muted">None of the songs you've played could be found in this library with a release year.</p>
        ) : (
          <>
            {latest && (
              <p className="mb-4 text-sm">
                In {latest.year}, half of what you played came out in <strong>{latest.median}</strong> or earlier
                {latest.year > latest.median ? ` (${years(latest.year - latest.median)} before)` : ''}.
              </p>
            )}
            <ShareGrid
              rows={t.decades}
              columns={columns}
              rowHeader="Released"
              caption="Share of each year's listens by the decade the music came out"
              note={(c) => `of ${formatCount(t.decade_matched[c])} listens with a release year`}
            />
            {ages.length > 1 && (
              <>
                <h3 className="mt-6 mb-2 text-sm font-medium">How old the music you played was</h3>
                <ColumnChart
                  points={ages.map((a) => ({ label: String(a.year), axis: String(a.year), value: Math.max(0, a.year - a.median) }))}
                  unit={(n) => `${years(n)} old (median)`}
                  caption="Median age of the music played, by year"
                  height={140}
                  labelSpacing={44}
                />
              </>
            )}
            <p className="mt-3 text-xs text-muted">
              A song's release year is the earliest year of the albums it's on in your library, so a remaster counts from
              the original. Based on {formatShare(withYear, totalListens)} of your listens.
            </p>
          </>
        )}
      </Card>
    </div>
  )
}
