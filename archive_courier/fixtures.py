"""Example work orders used by the CLI and tests.

A page is a list of tokens naming words by *slot*.  Versions share the same
public template and differ only in each opt token's chosen symbol.
"""


def demo_order(order_id="WO-2026-0001"):
    """Two options with deliberately different compression lengths."""
    return {
        "id": order_id,
        "pages": [
            [("pub", "the"), ("pub", "contract"), ("pub", "archive"),
             ("opt", "longword", {"public": 1, "protected": 300}),
             ("pub", "page"), ("pub", "records")],
            [("pub", "carrier"), ("pub", "transmits"),
             ("opt", "seal", {"public": 17, "protected": 2}),
             ("pub", "box"), ("pub", "the"), ("pub", "docket")],
            [("pub", "archive"), ("pub", "receives"), ("pub", "the"),
             ("opt", "box", {"public": 1, "protected": 4000}),
             ("pub", "docket"), ("pub", "page")],
        ],
    }


def larger_order(order_id="WO-2026-0002"):
    """More pages, still two options, to exercise multi-box packing."""
    pages = []
    for i in range(6):
        pages.append([
            ("pub", "the"), ("pub", "records"),
            ("opt", "longword", {"public": 1, "protected": 300}),
            ("pub", "page"), ("pub", "transmits"),
            ("pub", "the"), ("pub", "archive"),
        ])
        pages.append([
            ("pub", "carrier"), ("pub", "reviews"),
            ("opt", "seal", {"public": 17, "protected": 4000}),
            ("pub", "box"), ("pub", "the"), ("pub", "docket"),
            ("pub", "receives"), ("pub", "page"),
        ])
    return {"id": order_id, "pages": pages}
