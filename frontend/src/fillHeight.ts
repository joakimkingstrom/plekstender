import { useLayoutEffect, useRef, useState } from 'react'

const WIDE = '(min-width: 1024px)' // Tailwind's lg

/**
 * On a wide screen, a height for an element that takes it from where it starts down to the
 * bottom of the window (less `gap` px): for pages laid out to fit the window, whose columns
 * scroll inside. Narrower, nothing (the page scrolls as usual).
 */
export function useFillHeight<T extends HTMLElement>(gap = 16) {
  const ref = useRef<T>(null)
  const [height, setHeight] = useState<number | null>(null)
  useLayoutEffect(() => {
    // (Browsers all have these; tests and very old ones may not: then the page just scrolls.)
    if (typeof window.matchMedia !== 'function') return
    const wide = window.matchMedia(WIDE)
    const measure = () => {
      const el = ref.current
      if (!el || !wide.matches) return setHeight(null)
      const top = el.getBoundingClientRect().top + window.scrollY
      setHeight(Math.max(420, window.innerHeight - top - gap))
    }
    measure()
    window.addEventListener('resize', measure)
    wide.addEventListener('change', measure)
    // Banners above (an update, being offline) come and go.
    const watch = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    watch?.observe(document.body)
    return () => {
      window.removeEventListener('resize', measure)
      wide.removeEventListener('change', measure)
      watch?.disconnect()
    }
  }, [gap])
  return { ref, style: height === null ? undefined : { height } }
}
