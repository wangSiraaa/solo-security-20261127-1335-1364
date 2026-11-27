"""Packing engine: turn a work order into boxes and a public manifest.

A *work order* names words by stable slot.  Each token is either

    ("pub", slot)                       a plain public word
    ("opt", slot, {version: symbol})    a word plus a secret-option symbol

Several legal versions (options) share the same public template; they
differ only in the chosen ``symbol`` for each ``opt`` token.

All versions must yield an identical external record.  We therefore:

1. resolve every legal version to concrete integer triples
   ``(public_code, secret_symbol|None)`` using the dictionary;
2. take, per page, the *worst* encoded length over all versions;
3. assign pages to boxes using only those worst-case sizes and reserve a
   shape per box from them;
4. commit to the public plan (method, dictionary fingerprint, box shapes);
5. when actually shipping one version, encode it, fill each box, and pad to
   the reserved shape.

Because shapes come from the worst case over versions, every version fits
the same shapes and the sealed byte counts are version-independent.  A
version never gets an extra box for its own surplus.
"""

import hashlib
import json

from . import codec, container
from .util import ACError


# --------------------------------------------------------------------------
# work-order structure
# --------------------------------------------------------------------------
def legal_versions(order):
    versions = None
    for pi, page in enumerate(order["pages"]):
        for ti, tok in enumerate(page):
            if not isinstance(tok, (list, tuple)) or not tok:
                raise ACError(f"page {pi} token {ti}: malformed token")
            if tok[0] == "opt":
                if len(tok) != 3 or not isinstance(tok[2], dict) or not tok[2]:
                    raise ACError(f"page {pi} token {ti}: bad opt token")
                keys = tuple(sorted(tok[2]))
                if versions is None:
                    versions = keys
                elif keys != versions:
                    raise ACError(
                        f"page {pi} token {ti}: version set {keys} != {versions}")
    return list(versions) if versions is not None else ["default"]


def resolve(order, version, dictionary):
    """Resolve every page to (public_code, secret_symbol|None) triples."""
    out = []
    for pi, page in enumerate(order["pages"]):
        if not isinstance(page, (list, tuple)) or not page:
            raise ACError(f"page {pi}: must be a non-empty list of tokens")
        triples = []
        for ti, tok in enumerate(page):
            kind = tok[0]
            slot = tok[1] if len(tok) >= 2 else None
            if kind == "pub":
                if len(tok) != 2:
                    raise ACError(f"page {pi} token {ti}: bad pub token")
                triples.append((dictionary.code_of(slot), None))
            elif kind == "opt":
                if len(tok) != 3 or not isinstance(tok[2], dict):
                    raise ACError(f"page {pi} token {ti}: bad opt token")
                if version not in tok[2]:
                    raise ACError(
                        f"page {pi} token {ti}: no symbol for version {version!r}")
                symbol = tok[2][version]
                if not isinstance(symbol, int):
                    raise ACError(f"page {pi} token {ti}: symbol must be int")
                triples.append((dictionary.code_of(slot), symbol))
            else:
                raise ACError(f"page {pi} token {ti}: unknown kind {kind!r}")
        out.append(triples)
    return out


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
class Plan:
    def __init__(self, order_id, method, dict_fp, boxes, shapes, page_sizes):
        self.order_id = order_id
        self.method = method
        self.dict_fp = dict_fp
        self.boxes = boxes
        self.shapes = shapes
        self.page_sizes = page_sizes

    def n_boxes(self):
        return len(self.boxes)

    def canonical(self):
        return {
            "order": self.order_id,
            "method": self.method,
            "dictionary": self.dict_fp,
            "boxes": [{"seq": i, "plaintext": self.shapes[i]}
                      for i in range(len(self.shapes))],
        }

    def commitment(self):
        blob = json.dumps(self.canonical(), sort_keys=True,
                          separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


def _worst_page_sizes(order, method, versions, dictionary):
    resolved = [resolve(order, v, dictionary) for v in versions]
    n_pages = len(resolved[0])
    for v, p in zip(versions, resolved):
        if len(p) != n_pages:
            raise ACError(f"version {v!r} has {len(p)} pages, expected {n_pages}")
    sizes = []
    for pi in range(n_pages):
        sizes.append(max(codec.page_len(p[pi], method) for p in resolved))
    return sizes


def build_plan(order, method, dictionary, max_box_plain=None):
    """Reserve box shapes for *all* legal versions of the order."""
    if not isinstance(order, dict) or "id" not in order or "pages" not in order:
        raise ACError("order needs 'id' and 'pages'")
    if method not in ("layout", "shard"):
        raise ACError(f"unknown packing method {method!r}")
    versions = legal_versions(order)
    sizes = _worst_page_sizes(order, method, versions, dictionary)

    frame = container.PAGE_FRAME.size
    reserve = container.PAD_RESERVE if method == "layout" else 0

    if method == "shard":
        if max_box_plain is not None:
            for s in sizes:
                if frame + s + reserve > max_box_plain:
                    raise ACError(
                        f"a {s}B page does not fit a {max_box_plain}B box")
        boxes = [[i] for i in range(len(sizes))]
        shapes = [frame + sizes[i] + reserve for i in range(len(sizes))]
        return Plan(order["id"], method, dictionary.fingerprint(),
                    boxes, shapes, sizes)

    if max_box_plain is None:
        max_box_plain = frame * len(sizes) + sum(sizes) + reserve
    boxes, shapes = [], []
    cur, cur_len = [], reserve
    for i, s in enumerate(sizes):
        need = frame + s
        if reserve + need > max_box_plain:
            raise ACError(f"page {i} ({need}B) does not fit a {max_box_plain}B box")
        if cur and cur_len + need > max_box_plain:
            boxes.append(cur)
            shapes.append(cur_len)
            cur, cur_len = [], reserve
        cur.append(i)
        cur_len += need
    if cur:
        boxes.append(cur)
        shapes.append(cur_len)
    return Plan(order["id"], method, dictionary.fingerprint(),
                boxes, shapes, sizes)


# --------------------------------------------------------------------------
# transmit / receive
# --------------------------------------------------------------------------
def transmit(order, version, store, method, dictionary, max_box_plain=None):
    """Ship one legal ``version``; return the version-independent manifest."""
    if version not in legal_versions(order):
        raise ACError(f"version {version!r} is not legal for this order")
    plan = build_plan(order, method, dictionary, max_box_plain=max_box_plain)
    resolved = resolve(order, version, dictionary)
    frame = container.PAGE_FRAME.size

    for bi, idxs in enumerate(plan.boxes):
        need = sum(frame + codec.page_len(resolved[i], method) for i in idxs)
        if need > plan.shapes[bi]:
            raise ACError("internal: version exceeds reserved shape")

    for bi, idxs in enumerate(plan.boxes):
        blobs = [codec.encode_page(resolved[i], method) for i in idxs]
        body = container.pack_box(blobs)
        if method == "layout":
            body = container.pad_body(body, plan.shapes[bi])
        elif len(body) != plan.shapes[bi]:
            raise ACError("internal: shard box length mismatch")
        store.seal(f"{order['id']}/box-{bi:02d}", body)

    return manifest(plan, store)


def manifest(plan, store):
    boxes = []
    for bi in range(plan.n_boxes()):
        name = f"{plan.order_id}/box-{bi:02d}"
        info = store.peek(name)
        boxes.append({"seq": bi, "box": name, "bytes": info["bytes"]})
    return {
        "order": plan.order_id,
        "method": plan.method,
        "dictionary": plan.dict_fp,
        "boxes": boxes,
        "box_count": plan.n_boxes(),
        "commitment": plan.commitment(),
    }


def receive(manifest_obj, store, dictionary, method=None, strict_dict=True):
    """Recipient side: open boxes, return each page as a list of words."""
    method = method or manifest_obj["method"]
    if strict_dict:
        fp = manifest_obj.get("dictionary")
        if fp is not None and fp != dictionary.fingerprint():
            raise ACError(
                "manifest dictionary fingerprint does not match this codebook; "
                "use the codebook in force when the order was packed")
    rendered = []
    for box in manifest_obj["boxes"]:
        plain = store.open(box["box"])
        if method == "layout":
            blobs = container.strip_padding(plain)
        elif method == "shard":
            blobs = container.unpack_box(plain)
        else:
            raise ACError(f"unknown method {method!r}")
        for blob in blobs:
            _n, triples = codec.decode_page(blob, method)
            rendered.append(render_page(triples, dictionary))
    return rendered


def render_page(triples, dictionary):
    from .dictionary import option_word
    words = []
    for pub_code, secret in triples:
        base = dictionary.text_of_code(pub_code)
        if secret is None:
            words.append(base)
        else:
            words.append(f"{base}:{option_word(secret)}")
    return words
