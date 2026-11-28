import { useEffect, useState } from 'react'

/** Whether a media query matches (as it changes). */
function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => Boolean(window.matchMedia?.(query).matches))
  useEffect(() => {
    const media = window.matchMedia?.(query)
    if (!media?.addEventListener) return
    const changed = () => setMatches(media.matches)
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [query])
  return matches
}

/** Whether the system asks for less motion (as it changes). */
export function useReducedMotion(): boolean {
  return useMedia('(prefers-reduced-motion: reduce)')
}

/** Whether the window is wide enough for album covers behind the pages (Tailwind's md, 768
 * px): on a phone the page covers nearly all of them. */
export function useRoomForBackgrounds(): boolean {
  return useMedia('(min-width: 768px)')
}
