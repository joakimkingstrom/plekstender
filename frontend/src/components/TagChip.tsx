import { Link } from 'react-router'
import type { Schemas } from '../api/client'
import { KIND_LABELS, KIND_ORDER } from '../tagKinds'

/** Genres in the accent colour, styles outlined, moods soft and italic, countries plain. */
const TONES: Record<string, string> = {
  genre: 'bg-accent/15 text-accent hover:bg-accent/25',
  style: 'border border-border text-text hover:border-accent/60',
  mood: 'bg-surface-2 italic text-muted hover:text-text',
  country: 'bg-surface-2 text-muted hover:text-text',
}

/** A genre, style, mood or country, linked to its page in the library. */
export function TagChip({
  tag,
  showCount = false,
}: {
  tag: Pick<Schemas['TagOut'], 'id' | 'kind' | 'name'> & { count?: number | null }
  showCount?: boolean
}) {
  const kind = KIND_LABELS[tag.kind] ?? tag.kind
  return (
    <Link
      to={`/library/tags/${tag.id}`}
      title={kind}
      aria-label={`${kind}: ${tag.name}`}
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tag.kind] ?? TONES.country}`}
    >
      {tag.name}
      {showCount && tag.count ? <span className="ml-1 opacity-70 tabular-nums">{tag.count}</span> : null}
    </Link>
  )
}

/** An item's tags, genres first. */
export function TagChips({ tags, showCount = false }: { tags: Schemas['TagOut'][]; showCount?: boolean }) {
  if (!tags.length) return null
  const sorted = [...tags].sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind))
  return (
    <div className="flex flex-wrap gap-1.5">
      {sorted.map((t) => (
        <TagChip key={t.id} tag={t} showCount={showCount} />
      ))}
    </div>
  )
}
