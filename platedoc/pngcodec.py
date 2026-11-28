"""Dependency-free PNG reader/writer (stdlib only).

Reads every standard PNG colour type / bit depth (including Adam7 interlace)
and normalises to 8-bit RGB.  Writes plain, non-interlaced 8-bit RGB PNGs whose
only chunks are IHDR / IDAT / IEND, so the output is guaranteed to open in any
general-purpose image tool (ImageMagick, GIMP, libpng, browsers).
"""
import zlib
import struct

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class Image:
    __slots__ = ("width", "height", "pixels")

    def __init__(self, width, height, pixels):
        self.width = width
        self.height = height
        self.pixels = pixels  # list[list[(r,g,b)]]

    def crop(self, x0, y0, x1, y1):
        out = []
        for y in range(y0, y1):
            out.append(self.pixels[y][x0:x1])
        return Image(x1 - x0, y1 - y0, out)


# ----------------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------------
def _unfilter(raw, width, height, bpp, stride):
    """Reverse PNG scanline filters. Returns list of bytearrays (one/row)."""
    rows = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ft = raw[pos]
        pos += 1
        cur = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 0:
            pass
        elif ft == 1:  # Sub
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 0xFF
        elif ft == 3:  # Average
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                cur[i] = (cur[i] + pr) & 0xFF
        else:
            raise ValueError("bad filter type %d" % ft)
        rows.append(cur)
        prev = cur
    return rows


def _unpack_row(row, width, bitdepth, channels, palette):
    """Yield (r,g,b) tuples for one unfiltered scanline."""
    out = []
    if bitdepth == 8:
        for x in range(width):
            base = x * channels
            if channels == 1:
                g = row[base]
                out.append((g, g, g))
            elif channels == 2:
                g = row[base]
                out.append((g, g, g))
            elif channels == 3:
                out.append((row[base], row[base + 1], row[base + 2]))
            else:  # 4
                out.append((row[base], row[base + 1], row[base + 2]))
        return out
    if bitdepth == 16:
        for x in range(width):
            base = x * channels * 2
            vals = [row[base + 2 * c] << 8 | row[base + 2 * c + 1]
                    for c in range(channels)]
            if channels <= 2:
                g = vals[0] >> 8
                out.append((g, g, g))
            elif channels == 3:
                out.append((vals[0] >> 8, vals[1] >> 8, vals[2] >> 8))
            else:
                out.append((vals[0] >> 8, vals[1] >> 8, vals[2] >> 8))
        return out
    # sub-byte depths 1/2/4 (single channel or palette index)
    per_byte = 8 // bitdepth
    mask = (1 << bitdepth) - 1
    vals = []
    for x in range(width):
        byte = row[x // per_byte]
        shift = 8 - bitdepth * ((x % per_byte) + 1)
        vals.append((byte >> shift) & mask)
    if palette is not None:
        for v in vals:
            out.append(palette[v])
    else:
        scale = 255 // mask
        for v in vals:
            g = v * scale
            out.append((g, g, g))
    return out


def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG file: %s" % path)
    pos = 8
    idat = bytearray()
    width = height = bitdepth = colortype = interlace = None
    palette = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, comp, filt, interlace = \
                struct.unpack(">IIBBBBB", cdata)
            if comp != 0 or filt != 0:
                raise ValueError("unsupported compression/filter method")
        elif ctype == b"PLTE":
            palette = [(cdata[i], cdata[i + 1], cdata[i + 2])
                       for i in range(0, len(cdata), 3)]
        elif ctype == b"IDAT":
            idat += cdata
        elif ctype == b"IEND":
            break
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colortype]
    raw = zlib.decompress(bytes(idat))
    pixels = [[None] * width for _ in range(height)]

    if interlace == 0:
        bpp = max(1, (channels * bitdepth + 7) // 8)
        stride = (width * channels * bitdepth + 7) // 8
        rows = _unfilter(raw, width, height, bpp, stride)
        for y, row in enumerate(rows):
            pixels[y] = _unpack_row(row, width, bitdepth, channels, palette)
    else:
        # Adam7
        passes = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
                  (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
        bpp = max(1, (channels * bitdepth + 7) // 8)
        off = 0
        for (x0, y0, dx, dy) in passes:
            pw = (width - x0 + dx - 1) // dx if width > x0 else 0
            ph = (height - y0 + dy - 1) // dy if height > y0 else 0
            if pw == 0 or ph == 0:
                continue
            stride = (pw * channels * bitdepth + 7) // 8
            need = ph * (stride + 1)
            rows = _unfilter(raw[off:off + need], pw, ph, bpp, stride)
            off += need
            for j, row in enumerate(rows):
                vals = _unpack_row(row, pw, bitdepth, channels, palette)
                yy = y0 + j * dy
                for i, px in enumerate(vals):
                    pixels[yy][x0 + i * dx] = px
    return Image(width, height, pixels)


# ----------------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------------
def _chunk(ctype, cdata):
    return (struct.pack(">I", len(cdata)) + ctype + cdata +
            struct.pack(">I", zlib.crc32(ctype + cdata) & 0xFFFFFFFF))


def write_png(path, img):
    width, height = img.width, img.height
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: None
        row = img.pixels[y]
        for (r, g, b) in row:
            raw += bytes((r & 0xFF, g & 0xFF, b & 0xFF))
    comp = zlib.compress(bytes(raw), 9)
    out = bytearray(PNG_SIG)
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += _chunk(b"IDAT", comp)
    out += _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(out)


def new_image(width, height, color=(255, 255, 255)):
    return Image(width, height, [[color] * width for _ in range(height)])
