import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { errorMessage } from '../api/client'
import { useAlbumCover } from '../api/albumCovers'
import { imageUrl, libraryKeys, useActiveServer } from '../api/library'
import { useReducedMotion } from '../motion'
import { CoverEditor } from '../pages/AlbumCoversPage'
import { Dialog } from './Dialog'
import { Button, Notice, Spinner } from './ui'

type Album = {
  rating_key: number
  title: string
  thumb: string | null | undefined
  artist_title?: string | null
  video?: string | null
}

/**
 * An album's cover that opens larger when clicked, in a card: its video cover when it has
 * one. Server owners can look for a better cover from there (the sources, now and new side
 * by side, Save to Plex), without leaving the album.
 */
export function AlbumCoverView({ serverId, album, children, className = '' }: { serverId: number; album: Album; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false)
  const [finding, setFinding] = useState(false)
  const { active } = useActiveServer()
  const owner = active?.is_owner ?? false
  if (!album.thumb && !album.video) return <>{children}</>
  const close = () => {
    setOpen(false)
    setFinding(false)
  }
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label={`Show ${album.title} larger`} className={`block cursor-zoom-in ${className}`}>
        {children}
      </button>
      {open && (
        <Dialog
          title={album.title}
          subtitle={album.artist_title ?? undefined}
          onClose={close}
          // Most of the window, with a margin all round: the cover as big as the height allows
          // (the header, the button, and the padding take about 10.5rem), or the whole editor.
          panelClassName={
            finding
              ? 'h-[calc(100dvh-3rem)] w-[calc(100vw-2rem)] max-w-[120rem]'
              : 'max-h-[calc(100dvh-3rem)] w-[min(calc(100vw-2rem),calc(100dvh-10.5rem))]'
          }
          fill={finding}
        >
          {finding ? (
            <FindCover serverId={serverId} albumKey={album.rating_key} onBack={() => setFinding(false)} />
          ) : (
            <Large serverId={serverId} album={album} onFind={owner ? () => setFinding(true) : null} />
          )}
        </Dialog>
      )}
    </>
  )
}

/** The cover (or its video) as big as fits, and the way to a better one. */
function Large({ serverId, album, onFind }: { serverId: number; album: Album; onFind: (() => void) | null }) {
  const reduced = useReducedMotion()
  const src = album.thumb ? imageUrl(serverId, album.thumb, 1000) : null
  return (
    <div className="space-y-4">
      {album.video ? (
        <video
          src={album.video}
          poster={src ?? undefined}
          aria-label={`Video cover of ${album.title}`}
          controls
          muted
          loop
          playsInline
          autoPlay={!reduced}
          className="aspect-square w-full rounded-lg object-contain"
        />
      ) : (
        src && <img src={src} alt={`Cover of ${album.title}`} className="aspect-square w-full rounded-lg object-contain" />
      )}
      {onFind && (
        <div className="flex justify-center">
          <Button variant="secondary" onClick={onFind}>
            Find a better cover
          </Button>
        </div>
      )}
    </div>
  )
}

/** The album covers page's editor, for this album: back goes to the large cover. */
function FindCover({ serverId, albumKey, onBack }: { serverId: number; albumKey: number; onBack: () => void }) {
  const qc = useQueryClient()
  const cover = useAlbumCover(serverId, albumKey)
  if (cover.isPending) return <Spinner />
  if (cover.isError) return <Notice tone="error">{errorMessage(cover.error)}</Notice>
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CoverEditor
        serverId={serverId}
        album={cover.data}
        back="The cover"
        onBack={onBack}
        // Filling the dialog: its preview sizes the two covers from the height it gets.
        className="flex-1"
        // The album's page shows the new cover too.
        onSaved={() => void qc.invalidateQueries({ queryKey: libraryKeys.library(serverId) })}
      />
    </div>
  )
}
