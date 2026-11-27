"""Offline CLI: export a carrier manifest, then read it back as recipient.

State is local and split by role:

  warehouse file  opaque sealed blobs + the carrier-visible manifest.
                  Safe to hand to the carrier (it holds no option data).
  key file        the recipient key.  Never given to the carrier.

    python -m archive_courier export  --order demo --version public \\
        --out-warehouse wh.json --out-key key.json
    python -m archive_courier manifest --warehouse wh.json
    python -m archive_courier receive  --warehouse wh.json --key key.json
"""

import argparse
import json
import sys

from . import engine, fixtures
from .crypto import SealedStore
from .dictionary import Dictionary
from .util import ACError

_BOX_TAG = b"box-tag-00000000"
_DEFAULT_KEY = b"recipient-key-0000-demo-0000"


def _select_order(name):
    if name == "demo":
        return fixtures.demo_order()
    if name == "large":
        return fixtures.larger_order()
    raise ACError(f"unknown fixture {name!r}")


def cmd_export(args):
    order = _select_order(args.order)
    dictionary = Dictionary.from_name(args.dictionary)
    store = SealedStore(_DEFAULT_KEY, box_tag=_BOX_TAG)
    man = engine.transmit(order, args.version, store, args.method, dictionary,
                          max_box_plain=args.capacity)
    with open(args.out_warehouse, "w", encoding="utf-8") as fh:
        json.dump({"manifest": man, "boxes": store.export_blobs()}, fh, indent=2)
    with open(args.out_key, "w", encoding="utf-8") as fh:
        json.dump({"key_hex": _DEFAULT_KEY.hex(),
                   "box_tag_hex": _BOX_TAG.hex()}, fh, indent=2)
    print(json.dumps(man, indent=2))


def _load_warehouse(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _store_from(state, key):
    return SealedStore.from_blobs(key, state["boxes"], box_tag=_BOX_TAG)


def cmd_manifest(args):
    state = _load_warehouse(args.warehouse)
    print(json.dumps(state["manifest"], indent=2))


def cmd_receive(args):
    state = _load_warehouse(args.warehouse)
    with open(args.key, encoding="utf-8") as fh:
        key_info = json.load(fh)
    key = bytes.fromhex(key_info["key_hex"])
    store = _store_from(state, key)
    dictionary = Dictionary.from_name(args.dictionary)
    pages = engine.receive(state["manifest"], store, dictionary)
    for i, words in enumerate(pages):
        print(f"page {i}: {' '.join(words)}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="archive_courier")
    sub = parser.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="pack an order and export the manifest")
    e.add_argument("--order", default="demo", choices=["demo", "large"])
    e.add_argument("--version", default="public")
    e.add_argument("--method", default="layout", choices=["layout", "shard"])
    e.add_argument("--dictionary", default="tiny")
    e.add_argument("--capacity", type=int, default=None)
    e.add_argument("--out-warehouse", default="warehouse.json")
    e.add_argument("--out-key", default="key.json")
    e.set_defaults(func=cmd_export)

    m = sub.add_parser("manifest", help="show the carrier manifest")
    m.add_argument("--warehouse", default="warehouse.json")
    m.set_defaults(func=cmd_manifest)

    r = sub.add_parser("receive", help="read the order back as recipient")
    r.add_argument("--warehouse", default="warehouse.json")
    r.add_argument("--key", default="key.json")
    r.add_argument("--dictionary", default="tiny")
    r.set_defaults(func=cmd_receive)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ACError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
