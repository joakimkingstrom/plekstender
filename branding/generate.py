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


if __name__ == "__main__":
    write_svgs()
    write_pngs()
    print("wrote assets to", OUT)
