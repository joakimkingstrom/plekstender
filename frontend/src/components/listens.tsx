import { errorMessage, type Schemas } from '../api/client'
import { SOURCES, useDeleteListen, useListens, type ListenFilter } from '../api/listens'
import { formatCount, formatDay, formatTime } from '../format'
import { Dialog } from './Dialog'
import { Tooltip } from './Tooltip'
import { Badge, Button, Notice, Spinner } from './ui'

function groupByDay(items: Schemas['ListenOut'][]) {
  const days: { day: string; items: Schemas['ListenOut'][] }[] = []
  for (const item of items) {
    const day = formatDay(item.listened_at)
    if (days.at(-1)?.day !== day) days.push({ day, items: [] })
    days.at(-1)!.items.push(item)
  }
  return days
}

/** Listens grouped by day, newest first; optionally with a delete button per listen. */
export function ListenList({ items, deletable = false }: { items: Schemas['ListenOut'][]; deletable?: boolean }) {
  const remove = useDeleteListen()
  const located = items.some((i) => i.location && i.network === 'wan')
  return (
    <div className="space-y-6">
      {remove.isError && <Notice tone="error">{errorMessage(remove.error)}</Notice>}
      {groupByDay(items).map(({ day, items: dayItems }) => (
        <section key={day}>
          <h3 className="mb-2 text-sm font-semibold text-muted">{day}</h3>
          <ol className="divide-y divide-border rounded-2xl border border-border bg-surface">
            {dayItems.map((listen) => (
              <li key={listen.id} className="group flex items-center gap-3 px-4 py-2.5">
                <time dateTime={listen.listened_at} className="w-12 shrink-0 text-xs text-muted tabular-nums">
                  {formatTime(listen.listened_at)}
                </time>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{listen.track_name}</p>
                  <p className="truncate text-xs text-muted">
                    {listen.artist_name}
                    {listen.album_name && ` · ${listen.album_name}`}
                  </p>
                  {(listen.client || listen.location) && (
                    <p className="truncate text-[11px] text-muted/80">
                      {listen.client}
                      {listen.client && listen.location && ' · '}
                      {listen.location &&
                        (listen.address ? (
                          <Tooltip content={`IP address ${listen.address}`}>
                            <span>{listen.location}</span>
                          </Tooltip>
                        ) : (
                          listen.location
                        ))}
                    </p>
                  )}
                </div>
                {listen.duplicate && <Badge>Duplicate</Badge>}
                <Badge>{SOURCES[listen.source]}</Badge>
                {deletable && (
                  <Tooltip content="Delete this listen">
                    <button
                      type="button"
                      onClick={() => remove.mutate(listen.id)}
                      disabled={remove.isPending}
                      className="rounded px-1.5 text-muted opacity-60 hover:bg-surface-2 hover:text-danger group-hover:opacity-100"
                      aria-label={`Delete listen: ${listen.track_name} at ${formatTime(listen.listened_at)}`}
                    >
                      ×
                    </button>
                  </Tooltip>
                )}
              </li>
            ))}
          </ol>
        </section>
      ))}
      {located && (
        <p className="text-xs text-muted">
          Locations:{' '}
          <a href="https://db-ip.com" target="_blank" rel="noreferrer" className="underline">
            IP geolocation by DB-IP
          </a>
          , looked up on this server.
        </p>
      )}
    </div>
  )
}

/** A dialog with the listens behind something (a chart's bar, a heatmap square…). */
export function ListensDialog({
  title,
  filter,
  onClose,
}: {
  title: string
  filter: ListenFilter
  onClose: () => void
}) {
  const listens = useListens({ counted: true, ...filter })
  const total = listens.data?.pages[0]?.total
  return (
    <Dialog
      title={title}
      subtitle={total !== undefined ? `${formatCount(total)} ${total === 1 ? 'listen' : 'listens'}` : undefined}
      onClose={onClose}
    >
      {listens.isPending ? (
        <Spinner small />
      ) : listens.isError ? (
        <Notice tone="error">{errorMessage(listens.error)}</Notice>
      ) : total === 0 ? (
        <p className="text-sm text-muted">No listens here.</p>
      ) : (
        <>
          <ListenList items={listens.data.pages.flatMap((p) => p.items)} />
          {listens.hasNextPage && (
            <div className="mt-4 flex justify-center">
              <Button busy={listens.isFetchingNextPage} onClick={() => void listens.fetchNextPage()}>
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </Dialog>
  )
}
