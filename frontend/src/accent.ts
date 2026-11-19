/**
 * The accent colour, chosen per user. From one colour we derive what the theme needs:
 * a stronger shade for hovers, the text colour on top of it, and a chart colour that
 * keeps at least 3:1 contrast against the theme's surface (checked here, not eyeballed).
 */
export type Rgb = [number, number, number]
export type Theme = 'dark' | 'light'

/** The theme's own amber; a user without a choice keeps the stylesheet's values. */
export const BRAND = '#f2ad45'
const SURFACE: Record<Theme, string> = { dark: '#1f2025', light: '#ffffff' }
const INK = { dark: '#1c1d21', light: '#ffffff' }
const MIN_CONTRAST = 3

export const PRESETS: [string, string][] = [
  ['Amber', BRAND],
  ['Red', '#e5484d'],
  ['Pink', '#e93d82'],
  ['Purple', '#8e4ec6'],
  ['Blue', '#3e63dd'],
  ['Sky', '#0090ff'],
  ['Teal', '#12a594'],
  ['Green', '#30a46c'],
]

export function hexToRgb(hex: string): Rgb {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as Rgb
}

export function rgbToHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')}`
}

export function mix(a: string, b: string, amount: number): string {
  const x = hexToRgb(a)
  const y = hexToRgb(b)
  return rgbToHex(x.map((v, i) => v + (y[i] - v) * amount) as Rgb)
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio between two colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Nudge a colour towards white (dark theme) or black (light theme) until it reaches 3:1. */
function legible(color: string, theme: Theme): string {
  const target = theme === 'dark' ? '#ffffff' : '#000000'
  let out = color
  for (let step = 1; step <= 20 && contrast(out, SURFACE[theme]) < MIN_CONTRAST; step++) {
    out = mix(color, target, step * 0.05)
  }
  return out
}

export function accentTokens(color: string, theme: Theme): Record<string, string> {
  const accent = legible(color.toLowerCase(), theme)
  const strong = theme === 'dark' ? mix(accent, '#ffffff', 0.18) : mix(accent, '#000000', 0.15)
  const onAccent = contrast(accent, INK.dark) >= contrast(accent, INK.light) ? INK.dark : INK.light
  return {
    '--color-accent': accent,
    '--color-accent-strong': strong,
    '--color-on-accent': onAccent,
    '--color-chart': accent,
  }
}

/** Apply (or, with null, remove) a user's accent colour for the current theme. */
export function applyAccent(color: string | null | undefined, theme: Theme, root = document.documentElement) {
  const names = ['--color-accent', '--color-accent-strong', '--color-on-accent', '--color-chart']
  if (!color || color.toLowerCase() === BRAND) {
    names.forEach((n) => root.style.removeProperty(n))
    return
  }
  for (const [name, value] of Object.entries(accentTokens(color, theme))) root.style.setProperty(name, value)
}
