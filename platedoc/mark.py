"""Edition-mark payload coding and rendering.

Tile geometry (MARK_K=8 cells, MARK_CELL px per cell):
  * corner (0,0) = RED   anchor  -> tile coordinate origin
  * corner (K-1,0) = GREEN anchor -> defines +x  (cells per unit)
  * corner (0,K-1) = BLUE anchor  -> defines +y
  * corner (K-1,K-1) = LIGHT anchor (matches the light data colour)

All other 60 cells carry one payload bit: DARK=1, LIGHT=0.

Because the red/green/blue anchors have distinct colours, a received tile that
has been rotated by any multiple of 90 degrees is still decoded correctly: the
red centroid is the origin, and green/blue give the (rotated) cell basis.

Payload layout, MSB first, 38 significant bits then zero padding:
  comp(4) | edition(6) | tile_index(12) | crc16(16)
"""
from .common import (MARK_K, PAYLOAD_BITS, F_COMP, F_EDITION, F_TILE, F_CRC,
                     CORNER_CELLS, crc16, ANCHOR_TL, ANCHOR_TR, ANCHOR_BL,
                     ANCHOR_BR, CELL_DARK, CELL_LIGHT)


def encode_payload(comp, edition, tile_index):
    if not (0 <= comp < (1 << F_COMP)):
        raise ValueError("comp out of range")
    if not (0 <= edition < (1 << F_EDITION)):
        raise ValueError("edition out of range")
    if not (0 <= tile_index < (1 << F_TILE)):
        raise ValueError("tile out of range")
    body = (comp << (F_EDITION + F_TILE)) | (edition << F_TILE) | tile_index
    # crc over the significant body bytes (body needs ceil(26/8)=4 bytes)
    nbytes = (F_COMP + F_EDITION + F_TILE + 7) // 8
    body_bytes = [(body >> (8 * (nbytes - 1 - i))) & 0xFF for i in range(nbytes)]
    c = crc16(body_bytes)
    full = (body << F_CRC) | c
    bits = [(full >> (PAYLOAD_BITS - 1 - i)) & 1 for i in range(PAYLOAD_BITS)]
    bits += [0] * (60 - len(bits))
    return bits


def decode_bits(bits):
    full = 0
    for i in range(PAYLOAD_BITS):
        full = (full << 1) | (bits[i] & 1)
    c = full & 0xFFFF
    body = full >> F_CRC
    nbytes = (F_COMP + F_EDITION + F_TILE + 7) // 8
    body_bytes = [(body >> (8 * (nbytes - 1 - i))) & 0xFF for i in range(nbytes)]
    ok = crc16(body_bytes) == c
    comp = (body >> (F_EDITION + F_TILE)) & ((1 << F_COMP) - 1)
    edition = (body >> F_TILE) & ((1 << F_EDITION) - 1)
    tile = body & ((1 << F_TILE) - 1)
    return {"crc_ok": ok, "comp": comp, "edition": edition, "tile": tile,
            "crc": c}


def tile_cells(bits):
    cells = {}
    idx = 0
    for j in range(MARK_K):
        for i in range(MARK_K):
            if (i, j) in CORNER_CELLS:
                continue
            cells[(i, j)] = "D" if bits[idx] else "L"
            idx += 1
    return cells


def render_tile(comp, edition, tile_index, cell=4):
    bits = encode_payload(comp, edition, tile_index)
    cells = tile_cells(bits)
    special = {(0, 0): ANCHOR_TL, (MARK_K - 1, 0): ANCHOR_TR,
               (0, MARK_K - 1): ANCHOR_BL, (MARK_K - 1, MARK_K - 1): ANCHOR_BR}
    grid = []
    for j in range(MARK_K):
        row = []
        for i in range(MARK_K):
            if (i, j) in special:
                row.append(special[(i, j)])
            else:
                row.append(CELL_DARK if cells[(i, j)] == "D" else CELL_LIGHT)
        grid.append(row)
    out = []
    for j in range(MARK_K):
        for _ in range(cell):
            prow = []
            for i in range(MARK_K):
                prow.extend([grid[j][i]] * cell)
            out.append(prow)
    return out
