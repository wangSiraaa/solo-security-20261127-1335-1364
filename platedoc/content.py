"""Imaginary geometric plates for several compositions and editions.

Design contract (this is what makes the verification problem honest):

  * A *composition* (comp 0,1,2) has a fixed geometry AND a fixed palette.
  * Within a composition, EVERY edition is pixel-identical except for the
    edition marks.  So the same composition genuinely recurs across editions
    and the visible artwork cannot tell two editions apart -- only a readable
    edition mark can.  This is the "同一构图在各版反复出现 / 重复纹样" case.
  * Protected colour-discrimination patches keep *fixed* values.  Their values
    are supplied by the publication spec (so an authorised reissue may change
    them) and are always kept far (> the mark tolerance) from every mark
    colour.  Marks are never placed on or next to them ("不能标的位置").
"""
import random
from .pngcodec import Image
from .common import pick_colours, linf, min_dist_to_marks

PLATE_W, PLATE_H = 1248, 992
BLOCK = 32
GRID_COLS, GRID_ROWS = PLATE_W // BLOCK, PLATE_H // BLOCK      # 39 x 31

# Fixed protected rectangles (default publication).
DEFAULT_PROTECTED_RECTS = [
    (96, 96, 320, 256),
    (832, 576, 1120, 800),
    (448, 384, 672, 544),
]
# Fixed colour-discrimination values (default publication): two subtle pairs.
DEFAULT_PROTECTED_COLOURS = [
    (196, 140, 92), (200, 144, 96),      # warm pair
    (72, 156, 180), (76, 160, 184),      # teal pair
]
PROTECTED_BORDER = (128, 128, 128)

ROLE_NAMES = ["base", "accent", "band", "line", "disc", "ring"]


def _fill_rect(img, x0, y0, x1, y1, c):
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(img.width, x1); y1 = min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    band = [c] * (x1 - x0)
    for y in range(y0, y1):
        img.pixels[y][x0:x1] = band


def _fill_disc(img, cx, cy, r, c):
    if r <= 0:
        return
    r2 = r * r
    for y in range(max(0, cy - r), min(img.height, cy + r + 1)):
        dy = y - cy
        rem = r2 - dy * dy
        if rem < 0:
            continue
        span = int(rem ** 0.5)
        x0 = max(0, cx - span); x1 = min(img.width, cx + span + 1)
        if x1 > x0:
            img.pixels[y][x0:x1] = [c] * (x1 - x0)


def _ring(img, cx, cy, r, thick, c):
    r_out = r + thick
    for y in range(max(0, cy - r_out), min(img.height, cy + r_out + 1)):
        dy = y - cy
        row = img.pixels[y]
        for x in range(max(0, cx - r_out), min(img.width, cx + r_out + 1)):
            d2 = (x - cx) ** 2 + dy * dy
            if (r - thick) ** 2 <= d2 <= (r + thick) ** 2:
                row[x] = c


def _triangle(img, x0, y0, size, c, up=True):
    for yy in range(size):
        y = y0 + yy
        if not (0 <= y < img.height):
            continue
        half = yy if up else (size - 1 - yy)
        xa = x0 + half
        xb = x0 + size - 1 - half
        xa = max(0, xa); xb = min(img.width, xb + 1)
        if xb > xa:
            img.pixels[y][xa:xb] = [c] * (xb - xa)


def _style_stripes(img, cc):
    stripe_w, gap = 30, 36
    x = 12
    i = 0
    while x < PLATE_W:
        _fill_rect(img, x, 0, x + stripe_w, PLATE_H,
                   cc["accent"] if i % 2 else cc["band"])
        x += stripe_w + gap
        i += 1
    for k in range(0, PLATE_W, 64):
        for t in range(24):
            _fill_rect(img, k + t, 80 + t, k + t + 3, 120 + t, cc["line"])
    for rr in range(50, 300, 40):
        _ring(img, 980, 620, rr, 5, cc["ring"])
    for j in range(4):
        _fill_disc(img, 150 + j * 110, 860, 34, cc["disc"])


def _style_chevrons(img, cc):
    size = 34
    for gy in range(0, PLATE_H, size):
        for gx in range(0, PLATE_W, size):
            up = ((gx // size + gy // size) % 2) == 0
            _triangle(img, gx, gy, size,
                      cc["accent"] if up else cc["band"], up=up)
    for y in range(160, PLATE_H, 180):
        _fill_rect(img, 0, y, PLATE_W, y + 9, cc["line"])
    for j in range(3):
        _fill_disc(img, 980, 200 + j * 120, 30, cc["ring"])


def _style_nested(img, cc):
    for k in range(0, 10):
        inset = 12 + k * 26
        _fill_rect(img, inset, inset, PLATE_W - inset, PLATE_H - inset,
                   cc["accent"] if k % 2 else cc["band"])
    for x in range(0, PLATE_W, 72):
        _fill_rect(img, x, 0, x + 6, PLATE_H, cc["line"])
    for y in range(0, PLATE_H, 92):
        _fill_rect(img, 0, y, PLATE_W, y + 6, cc["line"])
    for j in range(5):
        _fill_disc(img, 1100 - j * 40, 900 - j * 40, 26, cc["disc"])


STYLES = {0: _style_stripes, 1: _style_chevrons, 2: _style_nested}


def comp_palette(comp, seed=20260101):
    """Fixed palette for a composition (identical for every edition).

    Compositions draw disjoint, well-separated colours so their piece
    histograms are distinguishable, and every colour stays far from the mark
    palette and from the protected colours.
    """
    used = list(DEFAULT_PROTECTED_COLOURS)
    # one large well-separated batch; slice per composition
    pool = pick_colours(18, used=used, sep=48)
    off = (comp * 6) % len(pool)
    sl = (pool[off:] + pool[:off])[:6]
    return {name: sl[i] for i, name in enumerate(ROLE_NAMES)}


def draw_protected(img, rects, colours):
    if not rects:
        return
    ca, cb = colours[0], colours[1]
    cc, cd = colours[2], colours[3]
    for (x0, y0, x1, y1) in rects:
        mid = (x0 + x1) // 2
        m2 = (y0 + y1) // 2
        for y in range(y0, y1):
            top = y < m2
            left = ca if top else cc
            right = cb if top else cd
            img.pixels[y][x0:mid] = [left] * (mid - x0)
            img.pixels[y][mid:x1] = [right] * (x1 - mid)
        b = PROTECTED_BORDER
        img.pixels[y0][x0:x1] = [b] * (x1 - x0)
        img.pixels[y1 - 1][x0:x1] = [b] * (x1 - x0)
        for y in range(y0, y1):
            img.pixels[y][x0] = b
            img.pixels[y][x1 - 1] = b


def build_plate(comp, protected_rects=None, protected_colours=None,
                seed=20260101):
    """Return (Image, palette).  Depends on comp+seed only, not edition."""
    protected_rects = (DEFAULT_PROTECTED_RECTS if protected_rects is None
                       else protected_rects)
    protected_colours = (DEFAULT_PROTECTED_COLOURS if protected_colours is None
                         else protected_colours)
    cc = comp_palette(comp, seed)
    img = Image(PLATE_W, PLATE_H,
                [[cc["base"]] * PLATE_W for _ in range(PLATE_H)])
    STYLES[comp % len(STYLES)](img, cc)
    draw_protected(img, protected_rects, protected_colours)
    return img, cc


def validate_protected_colours(colours, tol=64):
    """Protect teaching colours: must be far from every mark colour."""
    for c in colours:
        if min_dist_to_marks(c) <= tol:
            return False, c, min_dist_to_marks(c)
    # the two pairs ought to be *subtle* (close within pair, distinct across)
    if linf(colours[0], colours[1]) > 48 or linf(colours[2], colours[3]) > 48:
        return False, colours, "pair separation too large"
    return True, None, None
