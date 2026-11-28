"""Print-shop receiving seat: unwrap, verify, approve, impose.

The shop is disconnected from the sender: it holds only the editor's
*public* key, its own shop key, a local job table (expected edition per
job + which binding contract to use) and its binding-equipment contracts.
It never sees the sender's state.

For every mail in the local mailbox:

* a proof mail whose manifest signature verifies, whose edition matches
  the job table, whose pages match the signed hashes and which the local
  contract can physically realize -> impose, print, and sign a receipt;
* anything failing that -> no receipt (quarantine log);
* an ordinary mail without a manifest -> printed as usual, clearly marked
  UNAPPROVED (normal incoming work).
"""

import email
import email.policy
import json
import os

from . import contract as contract_mod
from . import imposition, keys, manifest as manifest_mod
from .image import Img, png_decode, normalized_hash
from .pdf import write_pdf


class Rejection(Exception):
    pass


def walk_parts(msg):
    """Yield every leaf part, descending into multipart/* and
    message/rfc822 (nested mails produced by ordinary mail editors)."""
    if msg.is_multipart():
        for p in msg.get_payload():
            yield from walk_parts(p)
    elif msg.get_content_type() == "message/rfc822":
        payload = msg.get_payload()
        if isinstance(payload, list):
            for sub in payload:
                yield from walk_parts(sub)
        yield msg
    else:
        yield msg


def _payload_bytes(part):
    data = part.get_payload(decode=True)
    if data is None:
        charset = part.get_content_charset() or "ascii"
        return part.get_payload().encode(charset, "surrogateescape")
    return data


def extract_proof(eml_path):
    """Pull the manifest, signature and images out of a mail."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    manifests = []
    sigs = []
    images = []
    for part in walk_parts(msg):
        ctype = part.get_content_type()
        filename = part.get_filename()
        if ctype == "application/json":
            raw = _payload_bytes(part)
            try:
                doc = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("profile") == manifest_mod.PROFILE:
                manifests.append((raw, doc))
        elif ctype == "application/octet-stream" and (
                (filename or "").endswith(".sig") or len(_payload_bytes(part)) == 64):
            sigs.append(_payload_bytes(part))
        elif ctype.startswith("image/"):
            raw = _payload_bytes(part)
            try:
                img = png_decode(raw)
            except Exception:
                continue
            images.append((filename, img))
    return manifests, sigs, images


def match_pages(manifest, images):
    """Multiset-match image parts to the signed page positions.

    Matching is by normalized content hash only -- filenames and part
    order are meaningless (attachments may share names, and gateways may
    reorder nothing but re-encode everything).  Returns
    {position: Img}. Raises Rejection if any signed page is missing."""
    by_hash = {}
    for filename, img in images:
        by_hash.setdefault(normalized_hash(img), []).append(img)
    pool = {h: list(v) for h, v in by_hash.items()}
    out = {}
    for pos, h in enumerate(manifest["page_order"], start=1):
        if pool.get(h):
            out[pos] = pool[h].pop()
        else:
            raise Rejection("page %d (hash %s...) not present in mail"
                            % (pos, h[:12]))
    return out


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _contract_by_id(shop_dir, cid):
    return _load_json(os.path.join(shop_dir, "contracts", cid + ".json"))


def _write_receipt(shop_dir, out_dir, manifest, manifest_bytes, verdict,
                   reason):
    receipt = {
        "shop": os.path.basename(shop_dir),
        "job": manifest.get("job"),
        "edition": manifest.get("edition"),
        "manifest_sha256": manifest_mod.manifest_sha256(manifest_bytes),
        "verdict": verdict,
        "reason": reason,
    }
    data = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode()
    sig = keys.sign(os.path.join(shop_dir, "keys", "shop.pem"), data)
    base = os.path.join(out_dir, "receipt-%s-ed%s" %
                        (manifest.get("job"), manifest.get("edition")))
    with open(base + ".json", "wb") as f:
        f.write(data)
    with open(base + ".sig", "wb") as f:
        f.write(sig)
    return base + ".json"


def _ordinary_print(images, out_dir, label):
    """Normal incoming job: print 1-up, clearly marked UNAPPROVED."""
    os.makedirs(out_dir, exist_ok=True)
    pages = []
    for i, (_fn, img) in enumerate(images, start=1):
        sheet = Img.filled(img.w + 80, img.h + 96, (255, 255, 255))
        sheet.rect(0, 0, sheet.w, sheet.h, (180, 180, 180), t=2)
        sheet.draw_text(40, 16, "UNAPPROVED ORDINARY PRINT %02d" % i,
                        (200, 30, 30), scale=2)
        sheet.draw_text(40, 40, label[:38], (60, 60, 60), scale=1)
        sheet.paste(img, 40, 72)
        pages.append(sheet)
    pdf = os.path.join(out_dir, "ordinary_print.pdf")
    write_pdf(pdf, pages)
    return pdf


def process_eml(shop_dir, eml_path, out_dir):
    """Process one received mail.  Returns a result dict."""
    os.makedirs(out_dir, exist_ok=True)
    jobs = _load_json(os.path.join(shop_dir, "jobs.json"))
    editor_pub = os.path.join(shop_dir, "keys", "editor.pub")
    shop = os.path.basename(shop_dir)
    result = {"mail": eml_path, "shop": shop, "verdict": None,
              "reason": None, "outputs": None}

    manifests, sigs, images = extract_proof(eml_path)
    if not manifests:
        # ordinary incoming mail: print as usual, without any approval
        pdf = _ordinary_print(images, out_dir,
                              os.path.basename(eml_path))
        result.update(verdict="ordinary", reason="no signed manifest",
                      outputs={"pdf": pdf})
        return result
    if len(manifests) > 1:
        raise Rejection("ambiguous: more than one manifest")
    manifest_bytes, manifest = manifests[0]
    if not sigs:
        raise Rejection("manifest without detached signature")
    if not any(keys.verify(editor_pub, manifest_bytes, s) for s in sigs):
        raise Rejection("signature does not verify")

    manifest_mod.check_manifest_structure(manifest)
    job = jobs.get(manifest["job"])
    if job is None:
        raise Rejection("unknown job %r" % manifest["job"])
    if job["edition"] != manifest["edition"]:
        raise Rejection("edition mismatch: manifest says %s, job table "
                        "expects %s" % (manifest["edition"], job["edition"]))
    images_by_pos = match_pages(manifest, images)
    contract = _contract_by_id(shop_dir, job["contract"])
    contract_mod.check_feasible(contract, manifest)

    label = "%s ED%s" % (manifest["job"].upper(), manifest["edition"])
    index = imposition.impose_book(manifest, images_by_pos, contract,
                                   out_dir, label)
    receipt = _write_receipt(shop_dir, out_dir, manifest, manifest_bytes,
                             "accepted", "signature, edition, pages and "
                             "binding feasibility all verified")
    result.update(verdict="accepted", reason="verified",
                  outputs={"index": index, "receipt": receipt},
                  approved_hashes=list(manifest["page_order"]),
                  manifest=manifest, contract=contract,
                  images_by_pos=images_by_pos)
    return result


def process_mailbox(shop_dir, mailbox_dir, out_root):
    results = []
    for name in sorted(os.listdir(mailbox_dir)):
        if not name.endswith(".eml"):
            continue
        eml = os.path.join(mailbox_dir, name)
        out_dir = os.path.join(out_root, os.path.splitext(name)[0])
        try:
            res = process_eml(shop_dir, eml, out_dir)
        except Rejection as e:
            os.makedirs(out_dir, exist_ok=True)
            res = {"mail": eml, "shop": os.path.basename(shop_dir),
                   "verdict": "rejected", "reason": str(e), "outputs": None}
            with open(os.path.join(out_dir, "REJECTED.txt"), "w") as f:
                f.write("rejected: %s\n" % e)
        results.append(res)
    log_path = os.path.join(out_root, "processing_log.json")
    os.makedirs(out_root, exist_ok=True)
    with open(log_path, "w") as f:
        json.dump([{k: v for k, v in r.items()
                    if k not in ("images_by_pos", "manifest", "contract")}
                   for r in results], f, indent=1, ensure_ascii=False)
    return results
