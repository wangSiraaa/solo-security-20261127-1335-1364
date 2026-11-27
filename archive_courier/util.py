"""Shared data types and helpers."""


class ACError(Exception):
    """Any failure that should abort a transmittal."""


# A page is a list of this token triples: (tag, public_code, secret_code)
#   tag        : structural class, must be public (shared by all options)
#   public_code: integer; encodes only the public template choice
#   secret_code: integer; encodes the secret option, never sent in clear
#
# Two legal options of a work order share every tag and every public_code,
# and differ only in secret_code values.
TOKEN = tuple  # (tag: str, public_code: int, secret_code: int)


def validate_page(page, where="page"):
    if not isinstance(page, (list, tuple)):
        raise ACError(f"{where}: page must be a list of tokens")
    if not page:
        raise ACError(f"{where}: empty page")
    for i, tok in enumerate(page):
        if not isinstance(tok, (list, tuple)) or len(tok) != 3:
            raise ACError(f"{where}[{i}]: token must be (tag, public, secret)")
        tag, p, s = tok
        if not isinstance(tag, str):
            raise ACError(f"{where}[{i}]: tag must be str")
        if not isinstance(p, int) or not isinstance(s, int):
            raise ACError(f"{where}[{i}]: codes must be ints")
        if p < 0 or s < 0:
            raise ACError(f"{where}[{i}]: codes must be non-negative")
    return [tuple(t) for t in page]


def validate_pages(pages, where="pages"):
    if not isinstance(pages, (list, tuple)) or not pages:
        raise ACError(f"{where}: must be a non-empty list of pages")
    return [validate_page(p, f"{where}[{i}]") for i, p in enumerate(pages)]
