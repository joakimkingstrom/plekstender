import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** An image (or, with `video`, a video cover with the image as its poster). */
export type LightboxImage = { src: string; alt: string; caption?: string; video?: string }

/**
 * A full-screen view of one image (or a set, with ← / →). Esc, the close button or a click
 * outside the image closes it; focus stays inside while it's open and returns afterwards.
 */
export function Lightbox({
  images,
  index: start = 0,
  onClose,
}: {
  images: LightboxImage[]
  index?: number
  onClose: () => void
}) {
  const [index, setIndex] = useState(start)
  const close = useRef<HTMLButtonElement>(null)
  // Where focus was before opening (read once, before the close button takes it).
  const [previous] = useState(() => document.activeElement as HTMLElement | null)
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })
  const image = images[index]

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current()
      else if (e.key === 'ArrowRight') setIndex((i) => Math.min(images.length - 1, i + 1))
      else if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1))
      else if (e.key === 'Tab') {
        e.preventDefault() // only the close button is focusable
        close.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [images.length, previous])

  if (!image) return null
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={image.alt}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/75 p-4 backdrop-blur-md"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <button
        ref={close}
        type="button"
        autoFocus
        onClick={onClose}
        aria-label="Close"
        className="absolute right-4 top-4 flex size-10 items-center justify-center rounded-full bg-white/10 text-xl text-white hover:bg-white/20"
      >
        ×
      </button>
      {image.video ? (
        <video
          src={image.video}
          poster={image.src}
          aria-label={image.alt}
          controls
          muted
          loop
          playsInline
          autoPlay={!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches}
          className="max-h-[85vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
        />
      ) : (
        <img src={image.src} alt={image.alt} className="max-h-[85vh] max-w-[92vw] rounded-lg object-contain shadow-2xl" />
      )}
      {(image.caption || images.length > 1) && (
        <p className="text-sm text-white/80">
          {image.caption}
          {images.length > 1 && ` · ${index + 1} of ${images.length}`}
        </p>
      )}
    </div>,
    document.body,
  )
}

/** Wraps an image so clicking it opens the lightbox. */
export function Zoomable({
  image,
  children,
  className = '',
}: {
  image: LightboxImage | null
  children: ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!image) return <>{children}</>
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Show ${image.alt} larger`}
        className={`block cursor-zoom-in ${className}`}
      >
        {children}
      </button>
      {open && <Lightbox images={[image]} onClose={() => setOpen(false)} />}
    </>
  )
}
