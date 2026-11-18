"""Generate the Plekstender logo assets (SVG + PNG + ICO + GIF) from one geometry definition.

The mark ("Tended"): a solid guitar pick in the ink colour (black on light,
white on dark) holding a half-filled amber star, a nod to half-star ratings.

Run from the repo root:  python3 branding/generate.py
Needs only Pillow.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent

# --- Brand tokens -----------------------------------------------------------
AMBER_LIGHT = "#d9821a"  # deeper amber: star on the white pick (dark theme)
AMBER_DARK = "#f2ad45"   # brighter amber: star on the black pick (light theme)
INK_LIGHT = "#1c1d21"    # pick + wordmark on light backgrounds
INK_DARK = "#f1efe9"     # pick + wordmark on dark backgrounds
PLATE_DARK = "#17181c"   # app icon / GIF background

# The star always uses the amber that contrasts with the pick it sits on.
LIGHT = dict(pick=INK_LIGHT, star=AMBER_DARK, bg="#ffffff")
DARK = dict(pick=INK_DARK, star=AMBER_LIGHT, bg=PLATE_DARK)

# --- Geometry (64-unit grid) -------------------------------------------------
# Guitar pick: wide rounded shoulders, softened tip.
PICK = [
    ("M", (32, 4)),
    ("C", (48, 4), (59, 9.5), (59, 22)),
    ("C", (59, 35.5), (44, 52), (35.5, 58)),
    ("C", (33.4, 59.5), (30.6, 59.5), (28.5, 58)),
    ("C", (20, 52), (5, 35.5), (5, 22)),
    ("C", (5, 9.5), (16, 4), (32, 4)),
]
STAR_C, STAR_R, STAR_r = (32, 27.5), 11.5, 5.0
STAR_STROKE = 2.4


def star_points(cx, cy, R, r):
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        rad = r if i % 2 else R
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def pick_d():
    parts = []
    for seg in PICK:
        cmd, *pts = seg
        parts.append(cmd + " ".join(f"{fmt(x)} {fmt(y)}" for x, y in pts))
    return "".join(parts) + "Z"


def star_d():
    pts = star_points(*STAR_C, STAR_R, STAR_r)
    return "M" + "L".join(f"{fmt(x)} {fmt(y)}" for x, y in pts) + "Z"


def pick_polygon(steps=48):
    pts, cur = [], None
    for cmd, *p in PICK:
        if cmd == "M":
            cur = p[0]
            pts.append(cur)
            continue
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cur, *p
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            pts.append((
                u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
            ))
        cur = p[-1]
    return pts


def clip_left(pts, cx):
    """Clip a polygon to x <= cx (Sutherland-Hodgman, one edge)."""
    out = []
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        a_in, b_in = a[0] <= cx, b[0] <= cx
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (cx - a[0]) / (b[0] - a[0])
            out.append((cx, a[1] + t * (b[1] - a[1])))
    return out


# --- SVG --------------------------------------------------------------------
# Classes let the adaptive files (favicon, loaders) recolour per theme; the
# presentation attributes carry the colours for the fixed files.
def star_svg(colors, clip_id):
    return (
        f'<path class="so" fill="none" stroke="{colors["star"]}" stroke-width="{STAR_STROKE}" '
        f'stroke-linejoin="miter" stroke-miterlimit="4" d="{star_d()}"/>'
        f'<path class="sf" fill="{colors["star"]}" clip-path="url(#{clip_id})" d="{star_d()}"/>'
    )


def clip_svg(clip_id, x=-8, y=-8, h=80):
    return f'<clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="{STAR_C[0] - x}" height="{h}"/></clipPath>'


def mark_svg(colors, clip_id="plekstender-half"):
    return (clip_svg(clip_id) + f'<path class="pk" fill="{colors["pick"]}" d="{pick_d()}"/>'
            + star_svg(colors, clip_id))


ADAPTIVE_STYLE = (
    f"<style>.pk{{fill:{LIGHT['pick']}}}.so{{stroke:{LIGHT['star']}}}.sf{{fill:{LIGHT['star']}}}"
    f"@media (prefers-color-scheme:dark){{.pk{{fill:{DARK['pick']}}}.so{{stroke:{DARK['star']}}}"
    f".sf{{fill:{DARK['star']}}}}}</style>"
)


def svg_file(body, viewbox="0 0 64 64", w=64, h=64, extra=""):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" width="{w}" height="{h}"{extra}>{body}</svg>\n'


def svg_mono():
    # One colour (currentColor): the star is cut through the pick instead.
    return svg_file(
        '<mask id="plekstender-cut" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64">'
        '<rect width="64" height="64" fill="#fff"/>' + clip_svg("plekstender-half")
        + star_svg(dict(star="#000"), "plekstender-half") + "</mask>"
        f'<path fill="currentColor" mask="url(#plekstender-cut)" d="{pick_d()}"/>'
    )


def svg_lockup(colors, ink):
    # Wordmark as live text (Bricolage Grotesque 700), one colour. Outline it
    # before using it anywhere the font isn't loaded.
    return svg_file(
        f'<g transform="translate(4 4)">{mark_svg(colors)}</g>'
        '<text x="82" y="50" font-family="\'Bricolage Grotesque\', \'Avenir Next\', system-ui, sans-serif" '
        f'font-weight="700" font-size="40" letter-spacing="-0.8" fill="{ink}">plekstender</text>',
        "0 0 340 72", 340, 72,
    )


def write_svgs():
    (OUT / "plekstender-mark.svg").write_text(svg_file(mark_svg(LIGHT)))
    (OUT / "plekstender-mark-dark.svg").write_text(svg_file(mark_svg(DARK)))
    (OUT / "plekstender-mark-mono.svg").write_text(svg_mono())
    (OUT / "favicon.svg").write_text(svg_file(ADAPTIVE_STYLE + mark_svg(LIGHT)))
    (OUT / "plekstender-lockup-light.svg").write_text(svg_lockup(LIGHT, INK_LIGHT))
    (OUT / "plekstender-lockup-dark.svg").write_text(svg_lockup(DARK, INK_DARK))


# --- Raster -----------------------------------------------------------------
SS = 8  # supersampling factor


def _star_offset_scale(d):
    """Scale that moves every star edge outwards by d (all edges sit at the same
    distance h from the centre, so an offset star is a scaled star)."""
    (ox, oy), (ix, iy) = star_points(0, 0, STAR_R, STAR_r)[:2]
    h = abs(ox * iy - oy * ix) / math.hypot(ix - ox, iy - oy)
    return (h + d) / h


def paint(img, to_px, unit, colors, pick_tf=lambda p: p, star_tf=lambda p: p):
    """Draw the mark. to_px maps grid -> pixels; pick_tf/star_tf move points in grid space.
    The star outline is drawn as a filled ring (outer star, then inner star in
    the pick colour), matching the SVG's mitred stroke."""
    d = ImageDraw.Draw(img)
    place = lambda pts: to_px(pick_tf(star_tf(pts)))
    d.polygon(to_px(pick_tf(pick_polygon())), fill=colors["pick"])
    for k, fill in ((_star_offset_scale(STAR_STROKE / 2), colors["star"]),
                    (_star_offset_scale(-STAR_STROKE / 2), colors["pick"])):
        d.polygon(place(star_points(*STAR_C, STAR_R * k, STAR_r * k)), fill=fill)
    d.polygon(place(clip_left(star_points(*STAR_C, STAR_R, STAR_r), STAR_C[0])), fill=colors["star"])


def render(size, colors, bg=None, scale=1.0, radius=0.0):
    """Mark centred on a square canvas. scale = mark height / canvas size."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if bg:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, big - 1, big - 1], radius=radius * big, fill=bg)
    # Pick spans y 4..59.5 (55.5 units) and x 5..59 (54 units); centre it.
    unit = scale * big / 55.5
    ox, oy = big / 2 - 32 * unit, big / 2 - 31.75 * unit
    paint(img, lambda pts: [(ox + x * unit, oy + y * unit) for x, y in pts], unit, colors)
    return img.resize((size, size), Image.LANCZOS)


def write_pngs():
    icons = OUT / "icons"
    icons.mkdir(exist_ok=True)
    # Transparent marks for each theme
    render(512, LIGHT, scale=0.94).save(icons / "mark-512.png")
    render(512, DARK, scale=0.94).save(icons / "mark-dark-512.png")
    # PWA "any" icons: dark plate, white pick
    render(192, DARK, PLATE_DARK, 0.66).save(icons / "icon-192.png")
    render(512, DARK, PLATE_DARK, 0.66).save(icons / "icon-512.png")
    # Maskable: full bleed, mark inside the 80% safe circle
    render(512, DARK, PLATE_DARK, 0.56).save(icons / "icon-maskable-512.png")
    # Apple touch icon: no transparency, iOS rounds the corners itself
    render(180, DARK, PLATE_DARK, 0.62).convert("RGB").save(icons / "apple-touch-icon.png")
    # favicon.ico: a bare black or white pick vanishes on one of the two tab
    # colours, so the fallback icon sits on a rounded dark tile.
    render(256, DARK, PLATE_DARK, 0.8, radius=0.2).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # Preview sheet for quick review: light row, dark row, app icons
    sheet = Image.new("RGBA", (720, 240), "#ffffff")
    ImageDraw.Draw(sheet).rectangle([0, 120, 360, 240], fill=PLATE_DARK)
    for row, colors in ((0, LIGHT), (120, DARK)):
        x = 20
        for s in (16, 32, 48, 96):
            im = render(s, colors, scale=0.98)
            sheet.paste(im, (x, row + 60 - s // 2), im)
            x += s + 24
    x = 380
    for name in ("icon-192.png", "icon-maskable-512.png"):
        sheet.paste(Image.open(icons / name).resize((150, 150), Image.LANCZOS), (x, 45))
        x += 170
    sheet.save(OUT / "preview.png")


# --- Animated loader ----------------------------------------------------------
# The pick strums: it rocks +-SWING degrees around a point near its top, and the
# star turns one point (72 deg) on each stroke. One cycle = two strokes.
LOOP_S = 1.2
SWING = 10
PIVOT = (32, 6)
EASE = "0.45 0 0.55 1"   # sine-like, for the swing
SNAP = "0.6 0 0.2 1"     # quick turn that settles, for the star


def svg_loader():
    cx, cy = STAR_C
    px, py = PIVOT
    return svg_file(
        ADAPTIVE_STYLE + clip_svg("plekstender-half", -8, -6, 72)
        + f'<g><animateTransform attributeName="transform" type="rotate" values="-{SWING} {px} {py};{SWING} {px} {py};-{SWING} {px} {py}" '
        f'keyTimes="0;0.5;1" calcMode="spline" keySplines="{EASE};{EASE}" dur="{LOOP_S}s" repeatCount="indefinite"/>'
        f'<path class="pk" fill="{LIGHT["pick"]}" d="{pick_d()}"/>'
        f'<g><animateTransform attributeName="transform" type="rotate" values="0 {cx} {cy};72 {cx} {cy};144 {cx} {cy}" '
        f'keyTimes="0;0.5;1" calcMode="spline" keySplines="{SNAP};{SNAP}" dur="{LOOP_S}s" repeatCount="indefinite"/>'
        + star_svg(LIGHT, "plekstender-half") + "</g></g>",
        "-8 -6 80 72", 80, 72, ' role="img" aria-label="Loading"',
    )


def _bezier(t, x1, y1, x2, y2):
    """CSS/SMIL cubic-bezier timing: progress at time fraction t."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        s = (lo + hi) / 2
        x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s**3
        lo, hi = (s, hi) if x < t else (lo, s)
    s = (lo + hi) / 2
    return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s**3


def _rot(pts, c, deg):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(c[0] + (x - c[0]) * ca - (y - c[1]) * sa, c[1] + (x - c[0]) * sa + (y - c[1]) * ca) for x, y in pts]


def loader_frame(size, t, colors):
    half, local = divmod(t * 2, 1)
    p = _bezier(local, *[float(v) for v in EASE.split()])
    swing = -SWING + 2 * SWING * p if half == 0 else SWING - 2 * SWING * p
    star_deg = 72 * (half + _bezier(local, *[float(v) for v in SNAP.split()]))

    big = size * SS
    unit = big / 80  # same 80-unit box as the SVG viewBox width
    ox, oy = 8 * unit, (8 + 4) * unit  # centre the 72-unit-tall box vertically
    img = Image.new("RGB", (big, big), colors["bg"])
    paint(img, lambda pts: [(ox + x * unit, oy + y * unit) for x, y in pts], unit, colors,
          pick_tf=lambda pts: _rot(pts, PIVOT, swing), star_tf=lambda pts: _rot(pts, STAR_C, star_deg))
    return img.resize((size, size), Image.LANCZOS)


def save_gif(path, frames, ms):
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True, disposal=1)


def write_loader(frames=36):
    (OUT / "plekstender-loader.svg").write_text(svg_loader())
    ms = round(LOOP_S * 1000 / frames)
    for name, colors in (("light", LIGHT), ("dark", DARK)):
        for size in (128, 256):
            save_gif(OUT / f"loader-{name}-{size}.gif",
                     [loader_frame(size, i / frames, colors).quantize(colors=64) for i in range(frames)], ms)


# --- Spinning loader ----------------------------------------------------------
# The pick spins in 3D around a vertical axis through its tip (x = 32), like a
# coin spun on its edge: the tip stays put while the shoulders swing round.
# On the back face the half-filled star shows mirrored, as it would in 3D.
SPIN_S = 1.6
SPIN_C = (32, 31.75)        # centre of the pick's bounding box
SPIN_EASE = "0.45 0 0.55 1"  # brief pause face-on, fastest edge-on
SPIN_SAMPLES = 48
MIN_SCALE = 0.03             # keep a sliver visible when edge-on


def spin_scale(t):
    sx = math.cos(math.radians(360 * _bezier(t, *[float(v) for v in SPIN_EASE.split()])))
    return math.copysign(max(abs(sx), MIN_SCALE), sx)


def svg_loader_spin():
    cx, cy = SPIN_C
    # SMIL can't animate a 3D rotation, so the turn is sampled as a horizontal
    # scale of cos(angle) around the axis.
    values = ";".join(f"{spin_scale(i / SPIN_SAMPLES):.3f} 1" for i in range(SPIN_SAMPLES + 1))
    return svg_file(
        ADAPTIVE_STYLE
        + f'<g transform="translate({cx} 0)"><g><animateTransform attributeName="transform" type="scale" '
        f'values="{values}" dur="{SPIN_S}s" repeatCount="indefinite"/>'
        f'<g transform="translate({-cx} 0)">{mark_svg(LIGHT)}</g></g></g>',
        f"0 {fmt(cy - 32)} 64 64", 64, 64, ' role="img" aria-label="Loading"',
    )


def spin_frame(size, t, colors):
    sx = spin_scale(t)
    big = size * SS
    unit = big / 64
    img = Image.new("RGB", (big, big), colors["bg"])
    paint(img, lambda pts: [(big / 2 + (x - SPIN_C[0]) * sx * unit, big / 2 + (y - SPIN_C[1]) * unit) for x, y in pts],
          unit * max(abs(sx), 0.35), colors)
    return img.resize((size, size), Image.LANCZOS)


def write_loader_spin(frames=48):
    (OUT / "plekstender-loader-spin.svg").write_text(svg_loader_spin())
    ms = round(SPIN_S * 1000 / frames)
    for name, colors in (("light", LIGHT), ("dark", DARK)):
        for size in (128, 256):
            save_gif(OUT / f"loader-spin-{name}-{size}.gif",
                     [spin_frame(size, i / frames, colors).quantize(colors=64) for i in range(frames)], ms)


if __name__ == "__main__":
    write_svgs()
    write_pngs()
    write_loader()
    write_loader_spin()
    print("wrote assets to", OUT)
