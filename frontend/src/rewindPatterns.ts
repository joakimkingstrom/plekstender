import { mix } from './accent'

/**
 * Rewind's backgrounds: a pattern drawn as an SVG for each slide, in the period's album-cover
 * colours (darkened, so white text stays readable). The kind of pattern changes from slide to
 * slide; small details (angles, sizes, where shapes fall) come from the slide's key, so a slide
 * always looks the same.
 */

export type Pattern = { backgroundColor: string; backgroundImage: string; backgroundSize: string; backgroundPosition: string }

const KINDS = ['stripes', 'dots', 'rings', 'waves', 'chevrons', 'confetti'] as const
export type PatternKind = (typeof KINDS)[number]

/** A small deterministic random number generator (mulberry32), seeded from a string. */
function seeded(key: string): () => number {
  let h = 2166136261
  for (const ch of key) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  let a = h >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const svgUrl = (svg: string) => `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
const round = (n: number) => Math.round(n * 10) / 10

/** The SVG for one pattern: `base` fills the background, `a` and `b` draw on it. */
export function patternSvg(kind: PatternKind, base: string, a: string, b: string, key: string): { svg: string; tile: boolean } {
  const r = seeded(key)
  switch (kind) {
    case 'stripes': {
      // Drawn once over the whole slide (a pattern inside one SVG), so the bands have no seams.
      const w = 36 + Math.floor(r() * 30)
      const angle = [30, 45, 60, 120, 135][Math.floor(r() * 5)]
      return {
        tile: false,
        svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice"><defs><pattern id="p" width="${w * 2}" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(${angle})"><rect width="${w * 2}" height="10" fill="${base}"/><rect width="${w}" height="10" fill="${a}"/><rect x="${round(w * 1.45)}" width="${round(w * 0.25)}" height="10" fill="${b}"/></pattern></defs><rect width="1000" height="1000" fill="url(#p)"/></svg>`,
      }
    }
    case 'dots': {
      const s = 44 + Math.floor(r() * 24)
      return {
        tile: true,
        svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}"><rect width="${s}" height="${s}" fill="${base}"/><circle cx="${s / 4}" cy="${s / 4}" r="${round(s * 0.17)}" fill="${a}"/><circle cx="${(s * 3) / 4}" cy="${(s * 3) / 4}" r="${round(s * 0.12)}" fill="${b}"/></svg>`,
      }
    }
    case 'rings': {
      const cx = round(200 + r() * 600)
      const cy = round(150 + r() * 700)
      const step = 46 + Math.floor(r() * 20)
      const rings = Array.from({ length: 28 }, (_, i) => {
        const rad = (28 - i) * step
        return `<circle cx="${cx}" cy="${cy}" r="${rad}" fill="${[base, a, b][i % 3]}"/>`
      }).join('')
      return { tile: false, svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice"><rect width="1000" height="1000" fill="${base}"/>${rings}</svg>` }
    }
    case 'waves': {
      const amp = 30 + r() * 40
      const len = 220 + r() * 160
      const gap = 70 + Math.floor(r() * 40)
      const wave = (y: number) => {
        let d = `M-50 ${round(y)}`
        for (let x = -50; x < 1100; x += len / 2) d += ` q${round(len / 4)} ${round(((x / (len / 2)) % 2 ? 1 : -1) * amp)} ${round(len / 2)} 0`
        return d
      }
      const paths = Array.from({ length: Math.ceil(1100 / gap) }, (_, i) => `<path d="${wave(i * gap)}" fill="none" stroke="${i % 2 ? a : b}" stroke-width="${round(gap * (i % 2 ? 0.45 : 0.18))}" stroke-linecap="round"/>`).join('')
      return { tile: false, svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice"><rect width="1000" height="1000" fill="${base}"/>${paths}</svg>` }
    }
    case 'chevrons': {
      const w = 60 + Math.floor(r() * 30)
      const h = w / 2
      return {
        tile: true,
        // A chevron band in `a`, with a thin line in `b` along its middle.
        svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${w}"><rect width="${w}" height="${w}" fill="${base}"/><path d="M0 ${h} L${w / 2} 0 L${w} ${h} L${w} ${w} L${w / 2} ${h} L0 ${w} Z" fill="${a}"/><path d="M0 ${round(w * 0.75)} L${w / 2} ${round(w * 0.25)} L${w} ${round(w * 0.75)}" fill="none" stroke="${b}" stroke-width="${round(w * 0.05)}"/></svg>`,
      }
    }
    case 'confetti': {
      const shapes = Array.from({ length: 70 }, () => {
        const x = round(r() * 1000)
        const y = round(r() * 1000)
        const size = round(18 + r() * 70)
        const fill = r() < 0.6 ? a : b
        const turn = Math.floor(r() * 360)
        const pick = r()
        if (pick < 0.35) return `<circle cx="${x}" cy="${y}" r="${round(size / 2)}" fill="${fill}"/>`
        if (pick < 0.7)
          return `<rect x="${x}" y="${y}" width="${size}" height="${round(size * 0.45)}" rx="${round(size * 0.22)}" fill="${fill}" transform="rotate(${turn} ${x} ${y})"/>`
        return `<path d="M${x} ${y} l${size} 0 l${round(-size / 2)} ${round(size * 0.87)} Z" fill="${fill}" transform="rotate(${turn} ${x} ${y})"/>`
      }).join('')
      return { tile: false, svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice"><rect width="1000" height="1000" fill="${base}"/>${shapes}</svg>` }
    }
  }
}

/** The background for slide `index` (with `key`), in `colour` and the next slide's colour. */
export function rewindBackground(index: number, key: string, colour: string, nextColour: string): Pattern {
  const kind = KINDS[index % KINDS.length]
  // Darkened, so the white text on top stays readable whatever the covers' colours.
  const base = mix(colour, '#000000', 0.62)
  const a = mix(colour, '#000000', 0.46)
  const b = mix(nextColour, '#000000', 0.4)
  const { svg, tile } = patternSvg(kind, base, a, b, key)
  return {
    backgroundColor: base,
    // A veil over the pattern keeps the text legible.
    backgroundImage: `linear-gradient(rgba(0,0,0,0.32), rgba(0,0,0,0.32)), ${svgUrl(svg)}`,
    backgroundSize: tile ? 'auto, auto' : 'cover, cover',
    backgroundPosition: 'center, center',
  }
}

export const PATTERN_KINDS = KINDS
