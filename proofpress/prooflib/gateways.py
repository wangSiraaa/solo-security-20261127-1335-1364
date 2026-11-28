"""The two offline mail gateways.

Gateway A ("sevenbit"): accepts 7-bit text only.  It re-encodes every leaf
part (text -> quoted-printable, everything else -> base64), re-chunks the
lines to a different width, re-wraps text content, re-folds headers, and
stamps its own header.  Decoded part content is preserved.

Gateway B ("eightbit"): allows 8-bit.  It passes attachments through,
converts text parts to 8bit, adds its own delivery note as an extra
top-level part plus a trace header.  MIME structure changes are its
business; the signed manifest bytes inside are untouched.

Both gateways work on raw message bytes and deliver to a local mailbox
directory as real .eml files.
"""

import base64
import email
import email.policy
import os
import quopri
import textwrap
from email.message import Message

POLICY_7BIT = email.policy.SMTP.clone(max_line_length=58)
POLICY_8BIT = email.policy.SMTP.clone(max_line_length=78)


def _decode_payload(part):
    data = part.get_payload(decode=True)
    if data is None:
        # 7bit/8bit/None CTE: payload is a str
        payload = part.get_payload()
        charset = part.get_content_charset() or "ascii"
        return payload.encode(charset, "surrogateescape")
    return data


def _b64(data, width):
    s = base64.b64encode(data).decode("ascii")
    return "\n".join(s[i:i + width] for i in range(0, len(s), width))


def _copy_headers(src, dst, extra):
    skip = {"content-type", "content-transfer-encoding", "mime-version",
            "content-disposition"}
    for k, v in src.items():
        if k.lower() in skip:
            continue
        dst[k] = v
    dst["MIME-Version"] = "1.0"
    for k, v in extra:
        dst[k] = v


# ------------------------------------------------------------- gateway A

def _gateway_a_part(part):
    new = Message()
    if part.is_multipart():
        new["Content-Type"] = part["Content-Type"]
        new.set_payload([_gateway_a_part(p) for p in part.get_payload()])
        return new
    raw = _decode_payload(part)
    ctype = part.get_content_type()
    maintype, _, subtype = ctype.partition("/")
    disp = part.get("Content-Disposition")
    if maintype == "text":
        charset = part.get_content_charset() or "utf-8"
        text = raw.decode(charset, "replace")
        # re-wrap the text itself (a legitimate thing for this gateway to
        # do to *display* text; nothing signed travels in text parts)
        lines = []
        for line in text.split("\n"):
            wrapped = textwrap.wrap(line, 64) or [""]
            lines.extend(wrapped)
        text = "\n".join(lines)
        new["Content-Type"] = "text/%s; charset=%s" % (subtype, charset)
        new["Content-Transfer-Encoding"] = "quoted-printable"
        new.set_payload(
            quopri.encodestring(text.encode(charset)).decode("ascii"))
    else:
        new["Content-Type"] = ctype
        if part.get_param("name"):
            new.set_param("name", part.get_param("name"))
        new["Content-Transfer-Encoding"] = "base64"
        new.set_payload(_b64(raw, 60))  # re-chunked to a different width
    if disp:
        new["Content-Disposition"] = disp
    return new


def gateway_a(in_path, out_path):
    with open(in_path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    new = Message()
    _copy_headers(msg, new, [("X-Gateway", "A/sevenbit: re-encoded, "
                              "re-folded to 7bit")])
    if msg.is_multipart():
        new["Content-Type"] = msg["Content-Type"]
        new.set_payload([_gateway_a_part(p) for p in msg.get_payload()])
    else:
        raise ValueError("gateway A expects a multipart message")
    data = new.as_bytes(policy=POLICY_7BIT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


# ------------------------------------------------------------- gateway B

def _gateway_b_part(part):
    new = Message()
    if part.is_multipart():
        new["Content-Type"] = part["Content-Type"]
        new.set_payload([_gateway_b_part(p) for p in part.get_payload()])
        return new
    raw = _decode_payload(part)
    ctype = part.get_content_type()
    maintype, _, subtype = ctype.partition("/")
    disp = part.get("Content-Disposition")
    if maintype == "text":
        charset = part.get_content_charset() or "utf-8"
        new["Content-Type"] = "text/%s; charset=%s" % (subtype, charset)
        new["Content-Transfer-Encoding"] = "8bit"
        new.set_payload(raw.decode(charset, "replace").encode(charset))
    else:
        # 8bit-capable: attachments pass through unchanged
        new["Content-Type"] = ctype
        if part.get_param("name"):
            new.set_param("name", part.get_param("name"))
        new["Content-Transfer-Encoding"] = "base64"
        new.set_payload(_b64(raw, 76))
    if disp:
        new["Content-Disposition"] = disp
    return new


def gateway_b(in_path, out_path, note=None):
    with open(in_path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    new = Message()
    _copy_headers(msg, new, [("X-Gateway", "B/eightbit: delivery note added")])
    note_part = Message()
    note_part["Content-Type"] = "text/plain; charset=utf-8"
    note_part["Content-Transfer-Encoding"] = "8bit"
    note_part.set_payload(
        (note or "送达说明：本件经乙网关（8位）投递；外层说明不构成编辑批示。\n"
         ).encode("utf-8"))
    if msg.is_multipart():
        # widen to mixed if needed, then prepend the delivery note
        ctype = msg["Content-Type"]
        if not ctype.lower().startswith("multipart/mixed"):
            ctype = 'multipart/mixed; boundary="GWB-MIX"'
        new["Content-Type"] = ctype
        new.set_payload([note_part]
                        + [_gateway_b_part(p) for p in msg.get_payload()])
    else:
        new["Content-Type"] = 'multipart/mixed; boundary="GWB-MIX"'
        new.set_payload([note_part, _gateway_b_part(msg)])
    data = new.as_bytes(policy=POLICY_8BIT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path
