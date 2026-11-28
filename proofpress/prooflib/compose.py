"""Sender-side encapsulation (option B: editor-signed parts manifest).

The editor signs the canonical manifest bytes; the mail carries

  multipart/mixed
  |-- multipart/related              (cover: displayable, NOT signed)
  |   |-- multipart/alternative
  |   |   |-- text/plain             (cover note + unsigned postscript)
  |   |   '-- text/html
  |   '-- image/png  logo.png        (decorative; not part of the proof)
  |-- application/json  manifest.json        (the signed bytes)
  |-- application/octet-stream manifest.sig  (detached Ed25519 signature)
  '-- image/png ...                  (proof pages; filenames may repeat)

Gateways may re-encode, re-fold and annotate; the receiver re-derives the
signed bytes from the decoded manifest part and matches pages by content
hash, so none of those legitimate transformations can break verification.
"""

import base64
import email.policy
import email.utils
import os
import quopri
from email.header import Header
from email.message import Message

from . import keys, manifest as manifest_mod


def _part(maintype, subtype, payload, cte=None, filename=None, charset=None):
    m = Message()
    m["Content-Type"] = "%s/%s" % (maintype, subtype)
    if filename is not None:
        m.add_header("Content-Disposition", "attachment", filename=filename)
        m.set_param("name", filename)
    if charset:
        m.set_param("charset", charset)
    if cte:
        m["Content-Transfer-Encoding"] = cte
    m.set_payload(payload)
    return m


def _qp(text, charset="utf-8"):
    return quopri.encodestring(text.encode(charset)).decode("ascii")


def _b64(data, width=76):
    s = base64.b64encode(data).decode("ascii")
    return "\n".join(s[i:i + width] for i in range(0, len(s), width))


def compose_proof(out_path, editor_priv, manifest, page_attachments,
                  cover_text, cover_html, logo_png_bytes,
                  from_addr, to_addr, subject):
    """Build and sign the proof mail.  page_attachments: list of
    (filename, png_bytes) in any order -- order and names carry no meaning.
    Returns the manifest bytes (also saved next to the mail)."""
    manifest_bytes = manifest_mod.canonical_bytes(manifest)
    sig = keys.sign(editor_priv, manifest_bytes)

    plain = _part("text", "plain", _qp(cover_text), "quoted-printable",
                  charset="utf-8")
    html = _part("text", "html", _qp(cover_html), "quoted-printable",
                 charset="utf-8")
    alt = Message()
    alt["Content-Type"] = 'multipart/alternative; boundary="ALT-BOUNDARY"'
    alt.set_payload([plain, html])
    logo = _part("image", "png", _b64(logo_png_bytes), "base64",
                 filename="logo.png")
    related = Message()
    related["Content-Type"] = 'multipart/related; boundary="REL-BOUNDARY"'
    related.set_payload([alt, logo])

    man = _part("application", "json", _b64(manifest_bytes), "base64",
                filename="manifest.json")
    man.set_param("charset", "utf-8")
    sigpart = _part("application", "octet-stream", _b64(sig), "base64",
                    filename="manifest.sig")

    pages = [_part("image", "png", _b64(data), "base64", filename=name)
             for name, data in page_attachments]

    msg = Message()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = Header(subject, "utf-8").encode()
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid("proofpress")
    msg["MIME-Version"] = "1.0"
    msg["Content-Type"] = 'multipart/mixed; boundary="MIX-BOUNDARY"'
    msg.set_payload([related, man, sigpart] + pages)

    data = msg.as_bytes(policy=email.policy.SMTP)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    # sender-side records (never shared with the receiving seat)
    with open(out_path + ".manifest.json", "wb") as f:
        f.write(manifest_bytes)
    with open(out_path + ".manifest.sig", "wb") as f:
        f.write(sig)
    return manifest_bytes


def compose_ordinary(out_path, from_addr, to_addr, subject, text,
                     image_attachments):
    """An ordinary nested mail with images (no manifest, no signature) --
    the kind of normal incoming job a shop prints without any approval."""
    plain = _part("text", "plain", _qp(text), "quoted-printable",
                  charset="utf-8")
    html = _part("text", "html", _qp("<p>%s</p>" % text),
                 "quoted-printable", charset="utf-8")
    alt = Message()
    alt["Content-Type"] = 'multipart/alternative; boundary="ALT2"'
    alt.set_payload([plain, html])
    pages = [_part("image", "png", _b64(d), "base64", filename=n)
             for n, d in image_attachments]
    msg = Message()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = Header(subject, "utf-8").encode()
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid("proofpress")
    msg["MIME-Version"] = "1.0"
    msg["Content-Type"] = 'multipart/mixed; boundary="MIX2"'
    msg.set_payload([alt] + pages)
    data = msg.as_bytes(policy=email.policy.SMTP)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return data
