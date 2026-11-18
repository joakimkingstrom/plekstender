"""Generate the roadie.fm logo assets (SVG + PNG + ICO) from one geometry definition.

Run from the repo root:  python3 branding/generate.py
Needs only Pillow.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent

# --- Brand tokens -----------------------------------------------------------
AMBER_LIGHT = "#d9821a"  # accent on light backgrounds
AMBER_DARK = "#f2ad45"   # accent on dark backgrounds
INK_LIGHT = "#1c1d21"    # text on light
INK_DARK = "#f1efe9"     # text on dark
PLATE_DARK = "#17181c"   # app icon background

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
STAR_C, STAR_R, STAR_r = (32, 26.5), 11.5, 5.0


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


# Even-odd fill-rule cuts the star out of the pick in a single path, no mask needed.
MARK_PATH = pick_d() + star_d()


# --- SVG --------------------------------------------------------------------
def svg_mark(fill, extra=""):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
        f'{extra}<path fill="{fill}" fill-rule="evenodd" d="{MARK_PATH}"/></svg>\n'
    )


ADAPTIVE_STYLE = (
    f"<style>path{{fill:{AMBER_LIGHT}}}"
    f"@media (prefers-color-scheme:dark){{path{{fill:{AMBER_DARK}}}}}</style>"
)


def svg_lockup(ink, accent):
    # Wordmark as live text (Bricolage Grotesque 700). Outline it before using
    # it anywhere the font isn't loaded.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 72" width="300" height="72">'
        f'<g transform="translate(4 4)"><path fill="{accent}" fill-rule="evenodd" d="{MARK_PATH}"/></g>'
        '<text x="82" y="50" font-family="\'Bricolage Grotesque\', \'Avenir Next\', system-ui, sans-serif" '
        f'font-weight="700" font-size="40" letter-spacing="-0.8" fill="{ink}">roadie'
        f'<tspan fill="{accent}">.fm</tspan></text></svg>\n'
    )


def write_svgs():
    (OUT / "roadie-mark.svg").write_text(svg_mark(AMBER_LIGHT))
    (OUT / "roadie-mark-dark.svg").write_text(svg_mark(AMBER_DARK))
    (OUT / "roadie-mark-mono.svg").write_text(svg_mark("currentColor"))
    (OUT / "favicon.svg").write_text(svg_mark(AMBER_LIGHT, ADAPTIVE_STYLE))
    (OUT / "roadie-lockup-light.svg").write_text(svg_lockup(INK_LIGHT, AMBER_LIGHT))
    (OUT / "roadie-lockup-dark.svg").write_text(svg_lockup(INK_DARK, AMBER_DARK))


# --- Raster -----------------------------------------------------------------
SS = 8  # supersampling factor


def render(size, color, bg=None, scale=1.0, radius=0.0):
    """Mark centred on a square canvas. scale = mark height / canvas size."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if bg:
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius * big, fill=bg)

    # Pick spans y 4..59.5 (55.5 units) and x 5..59 (54 units); centre it.
    unit = scale * big / 55.5
    ox = big / 2 - 32 * unit
    oy = big / 2 - 31.75 * unit
    tf = lambda pts: [(ox + x * unit, oy + y * unit) for x, y in pts]

    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(tf(pick_polygon()), fill=255)
    md.polygon(tf(star_points(*STAR_C, STAR_R, STAR_r)), fill=0)

    img.paste(Image.new("RGBA", (big, big), color), (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def write_pngs():
    icons = OUT / "icons"
    icons.mkdir(exist_ok=True)
    # Transparent marks
    render(512, AMBER_LIGHT, scale=0.94).save(icons / "mark-512.png")
    # PWA "any" icons: dark plate, generous mark
    render(192, AMBER_DARK, PLATE_DARK, 0.66).save(icons / "icon-192.png")
    render(512, AMBER_DARK, PLATE_DARK, 0.66).save(icons / "icon-512.png")
    # Maskable: full bleed, mark inside the 80% safe circle
    render(512, AMBER_DARK, PLATE_DARK, 0.56).save(icons / "icon-maskable-512.png")
    # Apple touch icon: no transparency, iOS rounds the corners itself
    render(180, AMBER_DARK, PLATE_DARK, 0.62).convert("RGB").save(icons / "apple-touch-icon.png")
    # favicon.ico: transparent, mark fills the square
    ico = render(256, AMBER_LIGHT, scale=0.98)
    ico.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # Preview sheet for quick review
    sheet = Image.new("RGBA", (720, 200), "#ffffff")
    x = 20
    for s in (16, 32, 48, 96):
        sheet.paste(render(s, AMBER_LIGHT, scale=0.98), (x, 100 - s // 2), render(s, AMBER_LIGHT, scale=0.98))
        x += s + 24
    for name in ("icon-192.png", "icon-maskable-512.png"):
        im = Image.open(icons / name).resize((160, 160), Image.LANCZOS)
        sheet.paste(im, (x, 20))
        x += 180
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
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -6 80 72" width="80" height="72" role="img" aria-label="Loading">
<style>.p{{fill:{AMBER_LIGHT}}}@media (prefers-color-scheme:dark){{.p{{fill:{AMBER_DARK}}}}}</style>
<mask id="roadie-star" maskUnits="userSpaceOnUse" x="-8" y="-6" width="80" height="72">
<rect x="-8" y="-6" width="80" height="72" fill="#fff"/>
<path fill="#000" d="{star_d()}"><animateTransform attributeName="transform" type="rotate" values="0 {cx} {cy};72 {cx} {cy};144 {cx} {cy}" keyTimes="0;0.5;1" calcMode="spline" keySplines="{SNAP};{SNAP}" dur="{LOOP_S}s" repeatCount="indefinite"/></path>
</mask>
<g><animateTransform attributeName="transform" type="rotate" values="-{SWING} {px} {py};{SWING} {px} {py};-{SWING} {px} {py}" keyTimes="0;0.5;1" calcMode="spline" keySplines="{EASE};{EASE}" dur="{LOOP_S}s" repeatCount="indefinite"/>
<path class="p" d="{pick_d()}" mask="url(#roadie-star)"/></g>
</svg>
'''


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


def loader_frame(size, t, color, bg):
    half, local = divmod(t * 2, 1)
    ease = [float(v) for v in EASE.split()]
    snap = [float(v) for v in SNAP.split()]
    p = _bezier(local, *ease)
    swing = -SWING + 2 * SWING * p if half == 0 else SWING - 2 * SWING * p
    star_deg = 72 * (half + _bezier(local, *snap))

    big = size * SS
    unit = big / 80  # same 80-unit box as the SVG viewBox width
    ox, oy = 8 * unit, (8 + 4) * unit  # centre the 72-unit-tall box vertically
    tf = lambda pts: [(ox + x * unit, oy + y * unit) for x, y in pts]

    star = _rot(star_points(*STAR_C, STAR_R, STAR_r), STAR_C, star_deg)
    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(tf(_rot(pick_polygon(), PIVOT, swing)), fill=255)
    md.polygon(tf(_rot(star, PIVOT, swing)), fill=0)

    img = Image.new("RGB", (big, big), bg)
    img.paste(Image.new("RGB", (big, big), color), (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def write_loader(frames=36):
    (OUT / "roadie-loader.svg").write_text(svg_loader())
    ms = round(LOOP_S * 1000 / frames)
    for name, color, bg in (("light", AMBER_LIGHT, "#ffffff"), ("dark", AMBER_DARK, PLATE_DARK)):
        for size in (128, 256):
            imgs = [loader_frame(size, i / frames, color, bg).quantize(colors=64) for i in range(frames)]
            imgs[0].save(OUT / f"loader-{name}-{size}.gif", save_all=True, append_images=imgs[1:],
                         duration=ms, loop=0, optimize=True, disposal=1)


# --- Spinning loader ----------------------------------------------------------
# The pick spins in 3D around a vertical axis through its tip (x = 32), like a
# coin spun on its edge: the tip stays put while the shoulders swing round.
# The pick is symmetric, so the back face looks the same as the front.
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
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {cy - 32} 64 64" width="64" height="64" role="img" aria-label="Loading">
<style>.p{{fill:{AMBER_LIGHT}}}@media (prefers-color-scheme:dark){{.p{{fill:{AMBER_DARK}}}}}</style>
<g transform="translate({cx} 0)"><g><animateTransform attributeName="transform" type="scale" values="{values}" dur="{SPIN_S}s" repeatCount="indefinite"/>
<path class="p" fill-rule="evenodd" transform="translate({-cx} 0)" d="{MARK_PATH}"/></g></g>
</svg>
'''


def spin_frame(size, t, color, bg):
    sx = spin_scale(t)
    big = size * SS
    unit = big / 64
    tf = lambda pts: [(big / 2 + (x - SPIN_C[0]) * sx * unit, big / 2 + (y - SPIN_C[1]) * unit) for x, y in pts]

    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(tf(pick_polygon()), fill=255)
    md.polygon(tf(star_points(*STAR_C, STAR_R, STAR_r)), fill=0)

    img = Image.new("RGB", (big, big), bg)
    img.paste(Image.new("RGB", (big, big), color), (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def write_loader_spin(frames=48):
    (OUT / "roadie-loader-spin.svg").write_text(svg_loader_spin())
    ms = round(SPIN_S * 1000 / frames)
    for name, color, bg in (("light", AMBER_LIGHT, "#ffffff"), ("dark", AMBER_DARK, PLATE_DARK)):
        for size in (128, 256):
            imgs = [spin_frame(size, i / frames, color, bg).quantize(colors=64) for i in range(frames)]
            imgs[0].save(OUT / f"loader-spin-{name}-{size}.gif", save_all=True, append_images=imgs[1:],
                         duration=ms, loop=0, optimize=True, disposal=1)


if __name__ == "__main__":
    write_svgs()
    write_pngs()
    write_loader()
    write_loader_spin()
    print("wrote assets to", OUT)
