"""Minimal RGB image type + self-contained PNG codec + normalization hashing.

No third-party dependencies: PNG encode/decode is implemented directly on
zlib (encoder emits 8-bit RGB, non-interlaced; decoder handles filter types
0-4 and color types 0/2/4/6, which is a superset of what the encoder emits
and of what any gateway in this system can produce -- gateways only rewrite
MIME transfer encodings, never pixel data).

"Normalization" of a page image = decode to raw RGB pixels and hash
sha256(width || height || pixels).  Re-encoding the MIME part (base64 <->
8bit <-> quoted-printable, line re-folding) never changes these bytes.
"""

import hashlib
import struct
import zlib

from . import font as _font


class Img:
    __slots__ = ("w", "h", "px")

    def __init__(self, w, h, px=None):
        self.w = w
        self.h = h
        self.px = px if px is not None else bytearray(w * h * 3)

    @classmethod
    def filled(cls, w, h, rgb):
        img = cls(w, h)
        img.px[:] = bytes(rgb) * (w * h)
        return img

    def copy(self):
        return Img(self.w, self.h, bytearray(self.px))

    def get(self, x, y):
        i = (y * self.w + x) * 3
        return self.px[i], self.px[i + 1], self.px[i + 2]

    def set(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i] = rgb[0]
            self.px[i + 1] = rgb[1]
            self.px[i + 2] = rgb[2]

    def fill_rect(self, x0, y0, x1, y1, rgb):
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(self.w, x1)
        y1 = min(self.h, y1)
        row = bytes(rgb) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 3
            self.px[i : i + len(row)] = row

    def rect(self, x0, y0, x1, y1, rgb, t=1):
        self.fill_rect(x0, y0, x1, y0 + t, rgb)
        self.fill_rect(x0, y1 - t, x1, y1, rgb)
        self.fill_rect(x0, y0, x0 + t, y1, rgb)
        self.fill_rect(x1 - t, y0, x1, y1, rgb)

    def hline(self, x0, x1, y, rgb, t=1):
        self.fill_rect(x0, y, x1, y + t, rgb)

    def vline(self, x, y0, y1, rgb, t=1):
        self.fill_rect(x, y0, x + t, y1, rgb)

    def circle(self, cx, cy, r, rgb, t=1):
        # midpoint circle, drawn as filled ring of thickness t
        r2 = r * r
        ri = (r - t) * (r - t)
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                d = (x - cx) * (x - cx) + (y - cy) * (y - cy)
                if ri <= d <= r2:
                    self.set(x, y, rgb)

    def crosshair(self, cx, cy, r, rgb, t=1):
        self.hline(cx - r, cx + r + 1, cy, rgb, t)
        self.vline(cx, cy - r, cy + r + 1, rgb, t)
        self.circle(cx, cy, max(2, r // 2), rgb, t)

    def draw_text(self, x, y, text, rgb, scale=1):
        cx = x
        for ch in text.upper():
            glyph = _font.FONT.get(ch, _font.FONT[" "])
            for gy, row in enumerate(glyph):
                for gx, c in enumerate(row):
                    if c == "#":
                        self.fill_rect(
                            cx + gx * scale,
                            y + gy * scale,
                            cx + (gx + 1) * scale,
                            y + (gy + 1) * scale,
                            rgb,
                        )
            cx += (_font.GLYPH_W + 1) * scale

    def paste(self, src, x0, y0):
        for y in range(src.h):
            dy = y0 + y
            if not (0 <= dy < self.h):
                continue
            sx0 = max(0, -x0)
            sx1 = min(src.w, self.w - x0)
            if sx1 <= sx0:
                continue
            si = (y * src.w + sx0) * 3
            di = (dy * self.w + x0 + sx0) * 3
            n = (sx1 - sx0) * 3
            self.px[di : di + n] = src.px[si : si + n]

    def rot90_cw(self, k=1):
        """Return a new image rotated k*90 degrees clockwise."""
        k %= 4
        img = self
        for _ in range(k):
            out = Img(img.h, img.w)
            for y in range(img.h):
                for x in range(img.w):
                    i = (y * img.w + x) * 3
                    nx, ny = img.h - 1 - y, x
                    j = (ny * out.w + nx) * 3
                    out.px[j : j + 3] = img.px[i : i + 3]
            img = out
        return img

    def crop(self, x0, y0, w, h):
        out = Img(w, h)
        for y in range(h):
            si = ((y0 + y) * self.w + x0) * 3
            di = y * w * 3
            out.px[di : di + w * 3] = self.px[si : si + w * 3]
        return out

    def center_crop(self, w, h):
        """Crop a w x h region around the center."""
        x0 = (self.w - w) // 2
        y0 = (self.h - h) // 2
        out = Img(w, h)
        for y in range(h):
            si = ((y0 + y) * self.w + x0) * 3
            di = y * w * 3
            out.px[di : di + w * 3] = self.px[si : si + w * 3]
        return out

    def hconcat(self, other):
        out = Img(self.w + other.w, max(self.h, other.h))
        out.paste(self, 0, 0)
        out.paste(other, self.w, 0)
        return out


# ---------------------------------------------------------------- PNG codec

def _chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_encode(img):
    raw = bytearray()
    stride = img.w * 3
    for y in range(img.h):
        raw.append(0)  # filter: none
        raw += img.px[y * stride : (y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", img.w, img.h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def png_decode(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = bytearray()
    w = h = None
    bitdepth = colortype = None
    interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            w, h, bitdepth, colortype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body
            )
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if bitdepth != 8 or interlace != 0:
        raise ValueError("unsupported PNG (need 8-bit, non-interlaced)")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
    raw = zlib.decompress(bytes(idat))
    stride = w * channels
    out = Img(w, h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p : p + stride])
        p += stride
        if f == 1:  # sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:  # up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:  # average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:  # paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        prev = line
        # expand to RGB
        if channels == 3:
            out.px[y * w * 3 : (y + 1) * w * 3] = line
        elif channels == 1:
            for x in range(w):
                out.px[(y * w + x) * 3 : (y * w + x) * 3 + 3] = bytes(
                    (line[x], line[x], line[x])
                )
        elif channels == 2:
            for x in range(w):
                g = line[x * 2]
                out.px[(y * w + x) * 3 : (y * w + x) * 3 + 3] = bytes((g, g, g))
        else:  # 4
            for x in range(w):
                out.px[(y * w + x) * 3 : (y * w + x) * 3 + 3] = line[
                    x * 4 : x * 4 + 3
                ]
    return out


def normalized_hash(img):
    """Hash of the normalized (decoded RGB pixel) content of a page image."""
    h = hashlib.sha256()
    h.update(b"PROOF-PAGE/1")
    h.update(struct.pack(">II", img.w, img.h))
    h.update(bytes(img.px))
    return h.hexdigest()


def load_png_file(path):
    with open(path, "rb") as f:
        return png_decode(f.read())


def save_png_file(img, path):
    with open(path, "wb") as f:
        f.write(png_encode(img))
