/** The guitar-pick mark (branding/plekstender-mark.svg) and its spin. */

export const PICK_PATH =
  'M32 4C48 4 59 9.5 59 22C59 35.5 44 52 35.5 58C33.4 59.5 30.6 59.5 28.5 58C20 52 5 35.5 5 22C5 9.5 16 4 32 4Z'
export const STAR_PATH =
  'M32 16L34.94 23.45L42.94 23.95L36.76 29.05L38.76 36.8L32 32.5L25.24 36.8L27.24 29.05L21.06 23.95L29.06 23.45Z'

// The brand's spinning pick (branding/plekstender-loader-spin.svg, also the
// loader-spin-*.gif): a 3D turn around the vertical axis through the tip, sampled as a
// horizontal scale, one turn in 1.6 s with a short pause face-on. The looping version is
// the .pick-spin class (index.css), from the same frames.
export const SPIN_SCALE = [1.000, 1.000, 1.000, 0.999, 0.997, 0.993, 0.985, 0.971, 0.949, 0.917, 0.872, 0.810, 0.728, 0.625, 0.499, 0.349, 0.178, -0.030, -0.209, -0.408, -0.597, -0.761, -0.890, -0.972, -1.000, -0.972, -0.890, -0.761, -0.597, -0.408, -0.209, -0.030, 0.178, 0.349, 0.499, 0.625, 0.728, 0.810, 0.872, 0.917, 0.949, 0.971, 0.985, 0.993, 0.997, 0.999, 1.000, 1.000, 1.000]
export const SPIN_MS = 1600
