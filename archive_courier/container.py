"""Container model: box shapes, whole-order windows, and in-box padding.

A *box shape* is a fixed plaintext capacity in bytes.  The carrier can see
the number of boxes, their order, and the sealed byte size of each — i.e.
the shape list.  The archive must use the *same* shape list for every legal
version of a work order, so the shape list is reserved from the worst case
over versions and every box is padded to its reserved shape.

Padding lives *inside* the sealed plaintext, so it is invisible to the
carrier; it only changes the sealed box's byte count to the reserved value,
which is already what the carrier sees.  The recipient knows a padding
marker (a frame length that cannot be a real page) and stops there.
"""

import struct

from .util import ACError

PAGE_FRAME = struct.Struct(">I")   # 4-byte page length prefix
PAD_MARK = struct.Struct(">I")     # same width as a frame length
PAD_MARK_VALUE = 0xFFFFFFFF        # impossible as a real page length
PAD_RESERVE = PAD_MARK.size        # room always left for the pad marker


def pack_box(blobs):
    body = bytearray()
    for blob in blobs:
        body += PAGE_FRAME.pack(len(blob))
        body += blob
    return bytes(body)


def unpack_box(body):
    pages = []
    pos = 0
    while pos < len(body):
        if pos + PAGE_FRAME.size > len(body):
            raise ACError("box body truncated at page frame")
        (n,) = PAGE_FRAME.unpack(body[pos:pos + PAGE_FRAME.size])
        pos += PAGE_FRAME.size
        if pos + n > len(body):
            raise ACError("box body truncated inside page")
        pages.append(body[pos:pos + n])
        pos += n
    return pages


def pad_body(body, target_len):
    if len(body) > target_len:
        raise ACError(f"body {len(body)}B exceeds reserved shape {target_len}B")
    if len(body) == target_len:
        return body
    need = target_len - len(body)
    if need < PAD_MARK.size:
        raise ACError("reserved shape leaves no room for pad marker")
    return body + PAD_MARK.pack(PAD_MARK_VALUE) + b"\x00" * (need - PAD_MARK.size)


def strip_padding(padded):
    pages = []
    pos = 0
    while pos < len(padded):
        if pos + PAGE_FRAME.size > len(padded):
            raise ACError("padded body truncated at page frame")
        (n,) = PAGE_FRAME.unpack(padded[pos:pos + PAGE_FRAME.size])
        if n == PAD_MARK_VALUE:
            return pages
        pos += PAGE_FRAME.size
        if pos + n > len(padded):
            raise ACError("padded body truncated inside page")
        pages.append(padded[pos:pos + n])
        pos += n
    return pages
