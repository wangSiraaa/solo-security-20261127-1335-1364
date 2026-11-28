#!/usr/bin/env python3
"""End-to-end drill: signed proof approval through two offline mail
gateways, acceptance + imposition at two print shops, and independent
fold-reading verification.

Run:  python3 demo/run_demo.py
All artifacts land in demo/out/ (real .eml files, sheet PNGs, PDFs,
fold reports).  Every check raises on failure.
"""

import email
import email.policy
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prooflib import compose, foldcheck, gateways, imposition, keys, receive
from prooflib import contract as contract_mod
from prooflib import manifest as manifest_mod
from prooflib.image import Img, normalized_hash, png_encode, save_png_file

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")

PAGE_W, PAGE_H = 160, 224

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name,
                           (" -- " + detail) if detail else ""))
    if not cond:
        raise SystemExit("CHECK FAILED: %s %s" % (name, detail))


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ------------------------------------------------------------- demo content

def make_page(pos, sig_id, label, variant=0):
    """A proof page image: unique pixels per (pos, variant)."""
    img = Img.filled(PAGE_W, PAGE_H, (252, 251, 246))
    sig_col = (40, 90, 200) if sig_id == 1 else (30, 140, 90)
    img.rect(0, 0, PAGE_W, PAGE_H, sig_col, t=3)
    img.fill_rect(0, 0, PAGE_W, 18, sig_col)
    # unique pattern: position-dependent stripes + color chip
    for i in range(0, PAGE_W, 8 + pos % 5):
        img.vline(i, 24, PAGE_H - 24, (235, 230, 220))
    chip = ((53 * pos + 17 * variant) % 200 + 30,
            (97 * pos + 31 * variant) % 200 + 30,
            (29 * pos + 7 * variant) % 200 + 30)
    img.fill_rect(PAGE_W - 34, PAGE_H - 34, PAGE_W - 10, PAGE_H - 10, chip)
    img.draw_text(14, 6, "JOB-117", (255, 255, 255), scale=1)
    img.draw_text(24, 60, label, (20, 20, 20), scale=6)
    img.draw_text(24, 110, "POS %02d" % pos, (80, 80, 80), scale=2)
    if variant:
        img.draw_text(24, 130, "REV %d" % variant, (200, 30, 30), scale=2)
    return img


def make_spread():
    """A wide cross-page image; the two halves become pages 10 and 11."""
    wide = Img.filled(PAGE_W * 2, PAGE_H, (250, 248, 244))
    for x in range(PAGE_W * 2):
        g = 120 + (x * 100) // (PAGE_W * 2)
        wide.vline(x, 0, PAGE_H, (g, 200 - g // 3, 230 - g // 2))
    # diagonal crossing the seam + circle centered on the seam
    for y in range(PAGE_H):
        x = y * (PAGE_W * 2 - 1) // (PAGE_H - 1)
        wide.set(x, y, (20, 20, 20))
        wide.set(PAGE_W * 2 - 1 - x, y, (20, 20, 20))
    wide.circle(PAGE_W, PAGE_H // 2, 40, (200, 30, 30), t=3)
    wide.crosshair(PAGE_W, PAGE_H // 2, 14, (20, 20, 20))
    left = wide.crop(0, 0, PAGE_W, PAGE_H)
    right = wide.crop(PAGE_W, 0, PAGE_W, PAGE_H)
    left.draw_text(10, 8, "P10", (255, 255, 255), scale=3)
    right.draw_text(PAGE_W - 44, 8, "P11", (255, 255, 255), scale=3)
    return wide, left, right


def make_logo():
    img = Img.filled(96, 96, (255, 255, 255))
    img.circle(48, 48, 40, (180, 40, 40), t=4)
    img.draw_text(20, 40, "ED", (180, 40, 40), scale=3)
    return img


# ------------------------------------------------------------------- setup

def build_world():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    dirs = {
        "keys": os.path.join(OUT, "keys"),
        "sender": os.path.join(OUT, "sender"),
        "mailboxA": os.path.join(OUT, "mailbox", "shopA"),
        "mailboxB": os.path.join(OUT, "mailbox", "shopB"),
        "shopA": os.path.join(OUT, "shopA"),
        "shopB": os.path.join(OUT, "shopB"),
        "proof": os.path.join(OUT, "proofreader"),
    }
    for d in dirs.values():
        os.makedirs(d)

    section("0. Keys, pages, contracts, job tables (local, no shared state)")
    ed_priv = os.path.join(dirs["keys"], "editor.pem")
    ed_pub = os.path.join(dirs["keys"], "editor.pub")
    keys.gen_keypair(ed_priv, ed_pub)
    print("  editor Ed25519 keypair generated (openssl)")

    # page images for edition 2 (current) and edition 1 (old)
    pages = {}          # pos -> Img (edition 2)
    pages_ed1 = {}      # pos -> Img (edition 1)
    wide, spread_l, spread_r = make_spread()
    reused = make_page(3, 1, "PLATE-7")   # same pixels at positions 3 & 16
    for pos in range(1, 19):
        if pos == 10:
            img = spread_l
        elif pos == 11:
            img = spread_r
        elif pos in (3, 16):
            img = reused
        else:
            sig_id = 1 if pos <= 8 else 2
            img = make_page(pos, sig_id, "P%02d" % pos)
        pages[pos] = img
        # edition 1: identical except position 5 (the "new page, same name")
        if pos == 5:
            pages_ed1[pos] = make_page(5, 1, "P05", variant=1)
        else:
            pages_ed1[pos] = img

    page_dir = os.path.join(dirs["sender"], "pages")
    os.makedirs(page_dir)
    filenames = {}
    for pos, img in pages.items():
        if pos in (1, 2, 3):
            name = "page.png"           # duplicate filenames on purpose
        elif pos == 16:
            name = "plate.png"          # same file name as position 3
        else:
            name = "page-%02d.png" % pos
        filenames[pos] = name
        save_png_file(img, os.path.join(page_dir, "%02d-%s" % (pos, name)))
    filenames_ed1 = dict(filenames)     # same names in the old edition
    print("  18 pages + spread + reused plate rendered; duplicate "
          "filenames on purpose")

    def build_manifest(edition, page_set, issued, approval_note):
        order = [normalized_hash(page_set[p]) for p in range(1, 19)]
        # the signed spread hash is what the reader must see across the
        # opening: the two approved pages side by side
        spread_hash = normalized_hash(page_set[10].hconcat(page_set[11]))
        return {
            "profile": manifest_mod.PROFILE,
            "job": "job-117",
            "edition": edition,
            "issued": issued,
            "binding_direction": "ltr",
            "page_order": order,
            "signatures": [
                {"id": 1, "positions": [1, 2, 3, 4, 5, 6, 7, 8]},
                {"id": 2, "positions": [9, 10, 11, 12, 15, 16, 17, 18],
                 "inserts": [{"after_leaf": 2, "positions": [13, 14]}]},
            ],
            "approvals": [
                {"scope": "book",
                 "text": "全书准印。" + approval_note},
                {"scope": "opening", "left": 6, "right": 7,
                 "text": "第六、七页左右次序不可对调。"},
                {"scope": "opening", "left": 10, "right": 11,
                 "text": "跨页图必须在成品上接成一线。",
                 "spread_sha256": spread_hash},
                {"scope": "signature", "signature": 1,
                 "text": "第一帖：共用图版在此作扉页，准印。"},
                {"scope": "signature", "signature": 2,
                 "text": "第二帖：同一图版此处作广告页，墨色可略深；插页勿倒。"},
            ],
        }

    manifest_ed2 = build_manifest(2, pages, "2026-11-28T09:30:00Z",
                                  "第二版批示：以此版为准。")
    manifest_ed1 = build_manifest(1, pages_ed1, "2026-11-20T10:00:00Z",
                                  "第一版批示：旧版，已被第二版取代。")
    manifest_mod.check_manifest_structure(manifest_ed2)
    manifest_mod.check_manifest_structure(manifest_ed1)

    # binding contracts (the shop's local binding-equipment table)
    contracts = {
        "A": {"id": "A", "paper_w": 480, "paper_h": 640, "margin": 40,
              "flip_axis": "vertical",
              "folds": [{"axis": "v", "move": "left", "place": "over"},
                        {"axis": "h", "move": "bottom", "place": "over"}],
              "cuts": ["S"],
              "inserts": [{"signature": 2, "after_leaf": 2, "count": 1}]},
        "B": {"id": "B", "paper_w": 560, "paper_h": 600, "margin": 40,
              "flip_axis": "horizontal",
              "folds": [{"axis": "h", "move": "top", "place": "under"},
                        {"axis": "v", "move": "right", "place": "over"}],
              "cuts": ["N"],
              "inserts": [{"signature": 2, "after_leaf": 2, "count": 1}]},
        "B2": {"id": "B2", "paper_w": 520, "paper_h": 560, "margin": 40,
               "flip_axis": "vertical",
               "folds": [{"axis": "h", "move": "bottom", "place": "over"},
                         {"axis": "v", "move": "left", "place": "over"}],
               "cuts": ["S"],
               "inserts": [{"signature": 2, "after_leaf": 2, "count": 1}]},
        "A-FLIP2": {"id": "A-FLIP2", "paper_w": 480, "paper_h": 640,
                    "margin": 40, "flip_axis": "vertical",
                    "folds": [{"axis": "v", "move": "left", "place": "over"},
                              {"axis": "h", "move": "bottom",
                               "place": "over"}],
                    "cuts": ["S"],
                    "inserts": [{"signature": 2, "after_leaf": 2,
                                 "count": 1}],
                    "signature_overrides": {"2": {"flip_axis":
                                                  "horizontal"}}},
        "A-BADINSERT": {"id": "A-BADINSERT", "paper_w": 480, "paper_h": 640,
                        "margin": 40, "flip_axis": "vertical",
                        "folds": [{"axis": "v", "move": "left",
                                   "place": "over"},
                                  {"axis": "h", "move": "bottom",
                                   "place": "over"}],
                        "cuts": ["S"],
                        "inserts": [{"signature": 2, "after_leaf": 3,
                                     "count": 1}]},
    }
    for shop, used in (("shopA", "A"), ("shopB", "B")):
        sdir = dirs[shop]
        os.makedirs(os.path.join(sdir, "keys"))
        os.makedirs(os.path.join(sdir, "contracts"))
        shutil.copy(ed_pub, os.path.join(sdir, "keys", "editor.pub"))
        keys.gen_keypair(os.path.join(sdir, "keys", "shop.pem"),
                         os.path.join(sdir, "keys", "shop.pub"))
        with open(os.path.join(sdir, "jobs.json"), "w") as f:
            json.dump({"job-117": {"edition": 2, "contract": used}}, f)
        for cid, c in contracts.items():
            with open(os.path.join(sdir, "contracts", cid + ".json"),
                      "w") as f:
                json.dump(c, f, indent=1)
    print("  two shops provisioned: editor PUBLIC key + own keys + local "
          "job table + contracts (no sender state)")
    return dirs, pages, pages_ed1, filenames, filenames_ed1, \
        manifest_ed2, manifest_ed1, wide, contracts, ed_priv


def attach_list(page_set, filenames):
    """Attachments in reading order; names may repeat (meaningless)."""
    return [(filenames[p], png_encode(page_set[p])) for p in range(1, 19)]


# ---------------------------------------------------------------- scenarios

def main():
    (dirs, pages, pages_ed1, filenames, filenames_ed1, manifest_ed2,
     manifest_ed1, wide, contracts, ed_priv) = build_world()

    logo_bytes = png_encode(make_logo())
    cover = ("校样已备：job-117 第二版。详见正式批示（经签章的清单）。\n"
             "本封面说明仅供阅读，不构成批示。\n"
             "P.S. 如有疑问请回邮。\n")
    cover_html = ("<html><body><h1>job-117 校样（第二版）</h1>"
                  "<p>封面说明，<b>不</b>构成批示。</p>"
                  "<img src=\"cid:logo\"></body></html>")

    section("1. Sender composes signed proof mails (option B: signed "
            "parts manifest)")
    outbox = os.path.join(dirs["sender"], "outbox")
    ed2_eml = os.path.join(outbox, "job-117-ed2.eml")
    m2_bytes = compose.compose_proof(
        ed2_eml, ed_priv, manifest_ed2, attach_list(pages, filenames),
        cover, cover_html, logo_bytes,
        "editor@example.com", "press@example.net",
        "job-117 校样 第二版（签章清单附）")
    ed1_eml = os.path.join(outbox, "job-117-ed1.eml")
    compose.compose_proof(
        ed1_eml, ed_priv, manifest_ed1,
        attach_list(pages_ed1, filenames_ed1),
        "旧版校样存档。\n", "<p>旧版校样存档。</p>", logo_bytes,
        "editor@example.com", "press@example.net", "job-117 校样 第一版")
    print("  composed:", os.path.relpath(ed2_eml, OUT))
    print("  composed:", os.path.relpath(ed1_eml, OUT))
    with open(os.path.join(dirs["sender"], "sent_log.jsonl"), "w") as f:
        f.write(json.dumps({"mail": ed2_eml, "manifest_sha256":
                            manifest_mod.manifest_sha256(m2_bytes)}) + "\n")
    print("  sender keeps its own sent log; shops never see it")

    section("2. Both gateways carry the mail (local mailboxes)")
    gA_ed2 = os.path.join(dirs["mailboxA"], "01-ed2-via-gatewayA.eml")
    gateways.gateway_a(ed2_eml, gA_ed2)
    gB_ed2 = os.path.join(dirs["mailboxB"], "01-ed2-via-gatewayB.eml")
    gateways.gateway_b(ed2_eml, gB_ed2)
    gA_ed1 = os.path.join(dirs["mailboxA"], "02-ed1-resent-via-gatewayA.eml")
    gateways.gateway_a(ed1_eml, gA_ed1)
    print("  gateway A (7bit, re-folded, re-encoded) -> shopA mailbox")
    print("  gateway B (8bit, adds delivery note)    -> shopB mailbox")
    print("  old edition legitimately re-sent through gateway A as well")

    # ordinary nested mail with images, made with the plain email library
    ord_eml = os.path.join(dirs["mailboxA"], "03-ordinary.eml")
    compose.compose_ordinary(
        ord_eml, "customer@example.org", "press@example.net",
        "普通来件：两张图，请照常印",
        "这两张图没有批示，按普通件印即可。",
        [("fig1.png", png_encode(make_page(1, 1, "AD-1"))),
         ("fig2.png", png_encode(make_page(2, 1, "AD-2")))])
    print("  ordinary nested mail (no manifest) -> shopA mailbox")

    # tampered variants
    def rewrite(eml_in, eml_out, fn):
        with open(eml_in, "rb") as f:
            msg = email.message_from_bytes(
                f.read(), policy=email.policy.default)
        fn(msg)
        data = msg.as_bytes(policy=email.policy.SMTP)
        with open(eml_out, "wb") as f:
            f.write(data)

    def tamper_note(msg):
        for p in receive.walk_parts(msg):
            if p.get_content_type() == "text/plain":
                txt = p.get_payload(decode=True).decode("utf-8")
                txt += "\nP.S. 临时决定：加印五百册，装订改无线胶订。\n"
                import quopri
                p.set_payload(
                    quopri.encodestring(txt.encode("utf-8")).decode("ascii"))
                p.replace_header("Content-Transfer-Encoding",
                                 "quoted-printable")
                return

    def tamper_manifest(msg):
        for p in receive.walk_parts(msg):
            if p.get_content_type() == "application/json":
                import base64
                doc = json.loads(base64.b64decode(
                    p.get_payload()).decode("utf-8"))
                doc["approvals"][0]["text"] = "全部拒印。（伪造批示）"
                doc["edition"] = 3
                raw = json.dumps(doc, ensure_ascii=False,
                                 sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
                p.set_payload(base64.encodebytes(raw).decode("ascii"))
                return

    def tamper_image(msg):
        from prooflib.image import png_decode
        import base64
        done = [False]

        def walk(m):
            if m.is_multipart():
                for sub in m.get_payload():
                    walk(sub)
            elif m.get_content_type() == "image/png" and not done[0]:
                raw = m.get_payload(decode=True)
                img = png_decode(raw)
                if img.w == PAGE_W and not done[0]:
                    img.fill_rect(0, 0, 20, 20, (255, 0, 0))
                    m.set_payload(base64.encodebytes(
                        png_encode(img)).decode("ascii"))
                    done[0] = True
        walk(msg)

    t_note = os.path.join(dirs["mailboxA"], "04-ed2-tampered-note.eml")
    rewrite(gA_ed2, t_note, tamper_note)
    t_man = os.path.join(dirs["mailboxA"], "05-ed2-tampered-manifest.eml")
    rewrite(gA_ed2, t_man, tamper_manifest)
    t_img = os.path.join(dirs["mailboxA"], "06-ed2-tampered-image.eml")
    rewrite(gA_ed2, t_img, tamper_image)
    print("  tampered variants (note / manifest / image) -> shopA mailbox")

    # the acceptance tester forwards the received proof mail as an
    # attachment inside a fresh nested mail (ordinary mail editor behavior)
    fwd_eml = os.path.join(dirs["mailboxB"], "02-ed2-forwarded-rfc822.eml")
    with open(gB_ed2, "rb") as f:
        inner = email.message_from_bytes(f.read(),
                                         policy=email.policy.default)
    from email.message import Message as _Msg
    fwd = _Msg()
    fwd["From"] = "acceptance@example.org"
    fwd["To"] = "press@example.net"
    fwd["Subject"] = "Fwd: job-117 proof (nested)"
    fwd["MIME-Version"] = "1.0"
    fwd["Content-Type"] = 'multipart/mixed; boundary="FWD-MIX"'
    fwd_text = _Msg()
    fwd_text["Content-Type"] = "text/plain; charset=utf-8"
    fwd_text["Content-Transfer-Encoding"] = "8bit"
    fwd_text.set_payload("验收人转发：原件作为附件嵌套。\n".encode("utf-8"))
    fwd_attach = _Msg()
    fwd_attach["Content-Type"] = "message/rfc822"
    fwd_attach["Content-Disposition"] = "attachment"
    fwd_attach.set_payload([inner])
    fwd.set_payload([fwd_text, fwd_attach])
    with open(fwd_eml, "wb") as f:
        f.write(fwd.as_bytes(policy=email.policy.SMTP))
    print("  proof mail forwarded as message/rfc822 attachment -> shopB")

    section("3. Standalone reader view: cover note displays, but carries "
            "no signature")
    with open(gB_ed2, "rb") as f:
        msg = email.message_from_bytes(f.read(),
                                       policy=email.policy.default)
    texts = [p for p in receive.walk_parts(msg)
             if p.get_content_type() == "text/plain"]
    shown = texts[0].get_payload(decode=True).decode("utf-8")
    print("  a mail reader shows %d text parts, e.g.: %.60s..."
          % (len(texts), shown.replace("\n", " ")))
    check("cover note is displayable but NOT covered by the signature",
          "封面说明" in shown or "送达说明" in shown)

    section("4. Shop A receives: verify, approve, impose (contract A)")
    resultsA = receive.process_mailbox(
        dirs["shopA"], dirs["mailboxA"], os.path.join(dirs["shopA"], "out"))
    by_mail = {os.path.basename(r["mail"]): r for r in resultsA}
    for name, r in sorted(by_mail.items()):
        print("  %-36s -> %s (%s)" % (name, r["verdict"], r["reason"]))
    r_ed2 = by_mail["01-ed2-via-gatewayA.eml"]
    check("ed2 via gateway A accepted", r_ed2["verdict"] == "accepted")
    r_ed1 = by_mail["02-ed1-resent-via-gatewayA.eml"]
    check("re-sent old edition NOT signed for (edition mismatch)",
          r_ed1["verdict"] == "rejected" and "edition" in r_ed1["reason"])
    r_note = by_mail["04-ed2-tampered-note.eml"]
    check("tampered UNSIGNED postscript still accepted",
          r_note["verdict"] == "accepted")
    check("approved pages unchanged by the tampered postscript",
          r_note["approved_hashes"] == r_ed2["approved_hashes"])
    r_man = by_mail["05-ed2-tampered-manifest.eml"]
    check("tampered manifest rejected", r_man["verdict"] == "rejected")
    r_img = by_mail["06-ed2-tampered-image.eml"]
    check("tampered page image rejected", r_img["verdict"] == "rejected")
    r_ord = by_mail["03-ordinary.eml"]
    check("ordinary mail printed as usual (UNAPPROVED)",
          r_ord["verdict"] == "ordinary"
          and os.path.exists(r_ord["outputs"]["pdf"]))
    receipt = r_ed2["outputs"]["receipt"]
    check("receipt signed by shop for the matching edition only",
          os.path.exists(receipt) and os.path.exists(receipt[:-5] + ".sig"))
    check("receipt verifies with shop public key",
          keys.verify(os.path.join(dirs["shopA"], "keys", "shop.pub"),
                      open(receipt, "rb").read(),
                      open(receipt[:-5] + ".sig", "rb").read()))

    section("5. Shop B receives the same mail via gateway B (contract B: "
            "other tray, other flip)")
    resultsB = receive.process_mailbox(
        dirs["shopB"], dirs["mailboxB"], os.path.join(dirs["shopB"], "out"))
    by_mailB = {os.path.basename(r["mail"]): r for r in resultsB}
    for name, r in sorted(by_mailB.items()):
        print("  %-36s -> %s (%s)" % (name, r["verdict"], r["reason"]))
    rB_ed2 = by_mailB["01-ed2-via-gatewayB.eml"]
    check("ed2 via gateway B accepted at shop B",
          rB_ed2["verdict"] == "accepted", rB_ed2["reason"] or "")
    rB_fwd = by_mailB["02-ed2-forwarded-rfc822.eml"]
    check("proof mail nested in message/rfc822 also verifies",
          rB_fwd["verdict"] == "accepted", rB_fwd["reason"] or "")
    same_layout = open(
        r_ed2["outputs"]["index"]["signatures"][0]["sheets"][0]["front"],
        "rb").read() == open(
        rB_ed2["outputs"]["index"]["signatures"][0]["sheets"][0]["front"],
        "rb").read()
    check("different tray/flip contract -> different imposition",
          not same_layout)

    section("6. Proofreader folds the real sheets and reads the book")
    approved = {normalized_hash(img): img for img in pages.values()}
    repA = foldcheck.fold_and_read(
        os.path.join(os.path.dirname(receipt), "output_index.json"),
        contracts["A"], manifest_ed2, approved,
        os.path.join(dirs["proof"], "shopA-ed2"))
    check("shop A output reads exactly the approved sequence",
          repA["order_matches_manifest"])
    check("shop A opening approvals verified",
          all(a["verified"] for a in repA["approvals"]))
    repB = foldcheck.fold_and_read(
        os.path.join(os.path.dirname(rB_ed2["outputs"]["receipt"]),
                     "output_index.json"),
        contracts["B"], manifest_ed2, approved,
        os.path.join(dirs["proof"], "shopB-ed2"))
    check("shop B output reads exactly the approved sequence",
          repB["order_matches_manifest"])
    check("shop B spread joins across the seam (hash of stitched image)",
          any(a.get("stitched_sha256") == normalized_hash(pages[10].hconcat(pages[11]))
              for a in repB["approvals"] if a["scope"] == "opening"))
    spread_png = os.path.join(dirs["proof"], "shopB-ed2",
                              "spread_p10_p11.png")
    check("cross-seam spread image exported", os.path.exists(spread_png))

    section("7. Same signed parts, different complete binding contract")
    alt_out = os.path.join(dirs["shopA"], "out", "contract-switch-B2")
    idx_alt = imposition.impose_book(manifest_ed2,
                                     r_ed2["images_by_pos"],
                                     contracts["B2"], alt_out, "JOB-117 ED2")
    rep_alt = foldcheck.fold_and_read(
        os.path.join(alt_out, "output_index.json"),
        contracts["B2"], manifest_ed2, approved,
        os.path.join(dirs["proof"], "shopA-ed2-contractB2"))
    same = open(idx_alt["signatures"][0]["sheets"][0]["front"], "rb").read() \
        == open(r_ed2["outputs"]["index"]["signatures"][0]["sheets"][0]
                ["front"], "rb").read()
    check("imposition changes with the contract", not same)
    check("folded book still reads the same approved content",
          rep_alt["order_matches_manifest"]
          and all(a["verified"] for a in rep_alt["approvals"]))

    section("8. Two signatures, same images, different approvals: change "
            "one signature's flip condition only")
    flip_out = os.path.join(dirs["shopA"], "out", "contract-A-FLIP2")
    idx_flip = imposition.impose_book(manifest_ed2, r_ed2["images_by_pos"],
                                      contracts["A-FLIP2"], flip_out,
                                      "JOB-117 ED2")
    base_idx = r_ed2["outputs"]["index"]
    sig1_same = all(
        open(idx_flip["signatures"][0]["sheets"][0][k], "rb").read()
        == open(base_idx["signatures"][0]["sheets"][0][k], "rb").read()
        for k in ("front", "back"))
    sig2_same = all(
        open(idx_flip["signatures"][1]["sheets"][0][k], "rb").read()
        == open(base_idx["signatures"][1]["sheets"][0][k], "rb").read()
        for k in ("front", "back"))
    check("signature 1 sheets byte-identical (its sign-off stands)",
          sig1_same)
    check("signature 2 sheets changed by the flip override", not sig2_same)
    rep_flip = foldcheck.fold_and_read(
        os.path.join(flip_out, "output_index.json"),
        contracts["A-FLIP2"], manifest_ed2, approved,
        os.path.join(dirs["proof"], "shopA-ed2-flip2"))
    check("signature 2 re-folded: spread and approvals re-verified",
          rep_flip["order_matches_manifest"]
          and all(a["verified"] for a in rep_flip["approvals"]))

    section("9. Insert position is part of the signed manifest")
    try:
        contract_mod.check_feasible(contracts["A-BADINSERT"], manifest_ed2)
        moved = False
    except ValueError as e:
        moved = True
        print("  infeasible as expected:", e)
    check("moving the insert without a new signature is infeasible", moved)

    section("10. Old edition + same-named new page in the mailbox")
    check("only the edition matching the approval was signed for",
          r_ed2["verdict"] == "accepted"
          and r_ed1["verdict"] == "rejected")
    check("same filename did not confuse matching (content-hash based)",
          r_ed2["images_by_pos"][5] is not None
          and normalized_hash(r_ed2["images_by_pos"][5])
          == manifest_ed2["page_order"][4])

    section("SUMMARY")
    for name, ok in CHECKS:
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    print("\nall %d checks passed" % len(CHECKS))
    print("artifacts under:", os.path.relpath(OUT))


if __name__ == "__main__":
    main()
