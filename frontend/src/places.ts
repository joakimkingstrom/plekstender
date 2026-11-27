/** Names and colour steps for the listens map. */

export const CONTINENTS: Record<string, string> = {
  AF: 'Africa',
  AN: 'Antarctica',
  AS: 'Asia',
  EU: 'Europe',
  NA: 'North America',
  OC: 'Oceania',
  SA: 'South America',
}

const regionNames = (() => {
  try {
    return new Intl.DisplayNames(undefined, { type: 'region' })
  } catch {
    return null
  }
})()

/** A country's name in the viewer's language (falls back to the code). */
export function countryName(code: string): string {
  try {
    return regionNames?.of(code) ?? code
  } catch {
    return code
  }
}

/** A country's flag as an emoji ("GB" → 🇬🇧): two regional indicator letters. Empty for
 * anything that isn't a two-letter code. (Windows shows the letters instead of a flag.) */
export function countryFlag(code: string): string {
  if (!/^[A-Za-z]{2}$/.test(code)) return ''
  return String.fromCodePoint(...[...code.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65))
}

/** Sequential steps of the chart colour over the surface (one hue, light to dark). */
export const STEPS = [28, 46, 64, 82, 100]

/** Which step a count falls in, or -1 for none (log scale, so a few big countries don't
 * wash out the rest). */
export function step(listens: number, max: number): number {
  if (listens <= 0 || max <= 0) return -1
  if (max === 1) return STEPS.length - 1
  const t = Math.log(listens) / Math.log(max)
  return Math.min(STEPS.length - 1, Math.floor(t * STEPS.length))
}
