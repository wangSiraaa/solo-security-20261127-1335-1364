"""Image transforms used both to synthesise plates and to simulate the
distortions a framer/apprentice may apply to a received piece.

Every operation here is one a general image tool performs, so the same output
can be reproduced with ImageMagick (see verify_interop.py).
"""
import math
from .pngcodec import Image


def crop(img, x0, y0, x1, y1):
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(img.width, x1)
    y1 = min(img.height, y1)
    return img.crop(x0, y0, x1, y1)


def rotate90(img):
    """Rotate 90 degrees clockwise."""
    w, h = img.width, img.height
    rows = [[None] * h for _ in range(w)]
    for y in range(h):
        for x in range(w):
            rows[x][h - 1 - y] = img.pixels[y][x]
    return Image(h, w, rows)


def rotate_k(img, k):
    k %= 4
    out = img
    for _ in range(k):
        out = rotate90(out)
    return out


def scale_nn(img, factor):
    """Nearest-neighbour integer upscale."""
    factor = int(factor)
    if factor <= 1:
        return Image(img.width, img.height, [r[:] for r in img.pixels])
    w, h = img.width, img.height
    rows = []
    for y in range(h):
        src = img.pixels[y]
        newrow = []
        for x in range(w):
            px = src[x]
            newrow.extend([px] * factor)
        for _ in range(factor):
            rows.append(newrow[:])
    return Image(w * factor, h * factor, rows)


def quantize(img, bits):
    """Merge low colour bits: keep `bits` significant bits per channel."""
    keep = bits
    drop = 8 - keep
    if drop <= 0:
        return Image(img.width, img.height, [r[:] for r in img.pixels])
    step = 1 << drop
    # round to nearest level centre
    def q(v):
        level = (v + step // 2) // step
        level = max(0, min((1 << keep) - 1, level))
        return level * step
    rows = [[(q(r), q(g), q(b)) for (r, g, b) in row] for row in img.pixels]
    return Image(img.width, img.height, rows)


def transform_piece(img, rotation=0, factor=1, quant_bits=8):
    out = rotate_k(img, rotation)
    out = scale_nn(out, factor)
    if quant_bits < 8:
        out = quantize(out, quant_bits)
    return out


def nearest_palette_colour(rgb, palette):
    best = None
    bd = 10 ** 9
    for c in palette:
        d = max(abs(rgb[0] - c[0]), abs(rgb[1] - c[1]), abs(rgb[2] - c[2]))
        if d < bd:
            bd, best = d, c
    return best


def recolour_region(img, rect, mapping):
    """Replace colours inside `rect` using mapping {old: new} (exact match)."""
    x0, y0, x1, y1 = rect
    for y in range(y0, min(y1, img.height)):
        for x in range(x0, min(x1, img.width)):
            px = img.pixels[y][x]
            if px in mapping:
                img.pixels[y][x] = mapping[px]
