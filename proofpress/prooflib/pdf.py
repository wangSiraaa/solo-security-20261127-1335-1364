"""Minimal multi-page PDF writer (image pages, Flate-compressed RGB).

Enough for generic viewers (each imposed sheet side becomes one PDF page).
No external dependencies.
"""

import zlib


def write_pdf(path, page_images):
    objects = []  # (number, body bytes)

    def add(body):
        objects.append(body)
        return len(objects)

    # reserve: 1 = catalog, 2 = pages
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")  # placeholder for pages object
    kids = []
    for img in page_images:
        raw = zlib.compress(bytes(img.px), 6)
        img_obj = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            b"/Filter /FlateDecode /Length %d >>\nstream\n" % (img.w, img.h, len(raw))
            + raw
            + b"\nendstream"
        )
        content = b"q\n%d 0 0 %d 0 0 cm\n/Im%d Do\nQ\n" % (img.w, img.h, img_obj)
        cont_obj = add(b"<< /Length %d >>\nstream\n" % len(content) + content
                       + b"\nendstream")
        page_obj = add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /XObject << /Im%d %d 0 R >> >> "
            b"/Contents %d 0 R >>" % (img.w, img.h, img_obj, img_obj, cont_obj)
        )
        kids.append(page_obj)
    objects[1] = (b"<< /Type /Pages /Count %d /Kids [" % len(kids)
                  + b" ".join(b"%d 0 R" % k for k in kids) + b"] >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_pos))
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path
