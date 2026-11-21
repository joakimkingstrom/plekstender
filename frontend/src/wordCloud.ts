/** Word cloud layout, kept apart from the component so it can be tested on its own. */

export interface CloudWord {
  word: string
  count: number
}

export interface Placed extends CloudWord {
  x: number // centre
  y: number
  w: number // the box it takes up (turned words: already swapped)
  h: number
  size: number // font size, px
  weight: 400 | 600 | 700
  tier: 0 | 1 | 2 // 0 small and muted, 2 big and bold
  vertical: boolean
}

export type Measure = (text: string, size: number, weight: number) => number

/** A steady hash, so a word lands in the same spot every time. */
function hash(s: string) {
  let h = 2166136261
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return h >>> 0
}

/** Lays words out as a cloud: the biggest first, near the middle, each next one spiralling
 * out from the centre (from its own starting angle) until it finds room; about one in six
 * smaller words turned on its side. Returns the words that fitted and the height used. */
export function layoutCloud(words: CloudWord[], width: number, measure: Measure): { placed: Placed[]; height: number } {
  if (!words.length || width <= 0) return { placed: [], height: 0 }
  const max = Math.max(...words.map((w) => w.count))
  const min = Math.min(...words.map((w) => w.count))
  const biggest = Math.min(50, Math.max(28, width / 8))
  const smallest = 13
  const shape = Math.max(1, Math.min(2.8, width / 300)) // wider than tall, like a cloud
  const placed: Placed[] = []
  const PAD = 3
  const sorted = [...words].sort((a, b) => b.count - a.count || a.word.localeCompare(b.word))
  sorted.forEach((word, i) => {
    // Square-root scale, so the few big words don't drown out the rest.
    const t = max === min ? 1 : Math.sqrt((word.count - min) / (max - min))
    const size = Math.round(smallest + t * (biggest - smallest))
    const tier = t > 0.45 ? 2 : t > 0.15 ? 1 : 0
    const weight = tier === 2 ? 700 : tier === 1 ? 600 : 400
    const h0 = hash(word.word)
    const vertical = i >= 3 && word.word.length <= 9 && h0 % 6 === 0
    const textW = measure(word.word, size, weight) + PAD * 2
    const textH = size * 1.05 + PAD
    const [w, h] = vertical ? [textH, textW] : [textW, textH]
    if (w > width) return
    const start = ((h0 % 360) * Math.PI) / 180
    for (let step = 0; step < 4000; step++) {
      const a = start + step * 0.35
      const r = step * 0.9
      const x = r * Math.cos(a) * shape
      const y = r * Math.sin(a)
      if (Math.abs(x) + w / 2 > width / 2) {
        if (r * shape > width) break // spiralled out past both sides: give up on this one
        continue
      }
      const clash = placed.some(
        (p) => Math.abs(p.x - x) * 2 < p.w + w && Math.abs(p.y - y) * 2 < p.h + h,
      )
      if (!clash) {
        placed.push({ ...word, x, y, w, h, size, weight, tier, vertical })
        return
      }
    }
  })
  const top = Math.min(...placed.map((p) => p.y - p.h / 2))
  const bottom = Math.max(...placed.map((p) => p.y + p.h / 2))
  for (const p of placed) {
    p.x += width / 2
    p.y -= top
  }
  return { placed, height: Math.ceil(bottom - top) }
}
