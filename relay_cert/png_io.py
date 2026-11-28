"""极简 PNG 读写（仅标准库），支持 RGB/RGBA、8 位、非隔行。

外部裁判用任意普通图像程序裁掉边上一格再存 PNG，本模块仍能逐像素读回，
认证流程不绑定任何专用软件。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def write_png(path: str | Path, pixels: list[list[tuple[int, int, int]]]) -> None:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # 每扫描行 filter type 0
        for r, g, b in row:
            raw.extend((r, g, b))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunks = [
        b"IHDR" + ihdr,
        b"IDAT" + zlib.compress(bytes(raw), 9),
        b"IEND",
    ]
    out = bytearray(PNG_SIG)
    for chunk in chunks:
        out += struct.pack(">I", len(chunk) - 4) + chunk
        out += struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    Path(path).write_bytes(bytes(out))


def read_png(path: str | Path) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    data = Path(path).read_bytes()
    if data[:8] != PNG_SIG:
        raise ValueError("不是合法 PNG 文件")
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError(f"仅支持 8 位 RGB/RGBA，收到 bit={bit_depth} type={color_type}")
    channels = 3 if color_type == 2 else 4
    bpp = channels
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[list[tuple[int, int, int]]] = []
    prev = [0] * stride
    i = 0
    for _ in range(height):
        ftype = raw[i]
        i += 1
        line = list(raw[i:i + stride])
        i += stride
        if ftype == 1:  # Sub
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + a) & 0xFF
        elif ftype == 2:  # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:  # Average
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        elif ftype != 0:
            raise ValueError(f"未知 PNG 滤波器 {ftype}")
        if channels == 3:
            rows.append([(line[x], line[x + 1], line[x + 2]) for x in range(0, stride, 3)])
        else:  # RGBA：透明像素按白底合成，普通裁图软件常见输出
            merged = []
            for x in range(0, stride, 4):
                r, g, b, al = line[x:x + 4]
                if al == 255:
                    merged.append((r, g, b))
                else:
                    merged.append(tuple(int(v * al / 255 + 255 * (1 - al / 255)) for v in (r, g, b)))
            rows.append(merged)
        prev = line
    return width, height, rows
