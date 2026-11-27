"""Asset references and the rule that decides which addresses are *shared*.

The wire mailbox refers to images in three ways:

``inline``
    Bytes embedded in the letter itself.  They were handed over with the
    letter, so using them costs no request at all.

``public``
    A *shared* illustration address.  Every reader of the same issue would
    ask for exactly the same path, so fetching it reveals nothing about who
    is reading.  These may be claimed once, at arrival, and sealed locally.

``subscriber``
    An address allocated to one subscriber.  Whoever fetches it tells the
    supply station which subscriber is reading.  These are **never**
    requested.

The important point for privacy is that ownership is decided by the
*addressing namespace*, never by the bytes.  Two illustrations may look
identical (even be byte-identical) and still have different owners; only the
URL decides whether it is safe to fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

INLINE = "inline"
PUBLIC = "public"
SUBSCRIBER = "subscriber"
UNKNOWN = "unknown"

#: Only URLs below this path are treated as shared/public.
PUBLIC_PREFIX = "/public/"
#: URLs below this path are bound to a single subscriber.
SUBSCRIBER_PREFIX = "/sub/"


class AssetError(ValueError):
    """Raised when an asset reference is malformed or unsafe to fetch."""


def classify_url(url: str) -> str:
    """Return the kind of an address, based purely on its namespace.

    Deliberately *not* based on the image content: identical pictures can be
    owned by different parties.
    """
    if not url:
        return UNKNOWN
    path = urlsplit(url).path or ""
    if path.startswith(PUBLIC_PREFIX):
        return PUBLIC
    if path.startswith(SUBSCRIBER_PREFIX):
        return SUBSCRIBER
    return UNKNOWN


def is_shared_public_url(url: str) -> bool:
    """True only for addresses that are safe to request on behalf of anyone."""
    return classify_url(url) == PUBLIC


def public_path(issue_id: str, asset_id: str) -> str:
    """Canonical request path for a shared illustration."""
    return f"{PUBLIC_PREFIX}{issue_id}/{asset_id}"


@dataclass(frozen=True)
class AssetRef:
    """A parsed reference to an image inside a letter."""

    asset_id: str
    kind: str
    role: str = "decor"
    url: str = ""
    media_type: str = ""
    alt: str = ""
    bytes_b64: str = ""

    @property
    def is_map(self) -> bool:
        return self.role == "map"


def parse_asset(raw: dict) -> AssetRef:
    if not isinstance(raw, dict):
        raise AssetError("asset entry must be an object")
    asset_id = raw.get("asset_id")
    if not asset_id or not isinstance(asset_id, str):
        raise AssetError("asset needs a non-empty string asset_id")
    kind = raw.get("kind")
    if kind not in (INLINE, PUBLIC, SUBSCRIBER):
        raise AssetError(f"asset {asset_id!r} has unknown kind {kind!r}")
    url = raw.get("url", "") or ""
    if kind in (PUBLIC, SUBSCRIBER):
        classified = classify_url(url)
        if classified != kind:
            # Defence in depth: a reference that *claims* to be public but
            # points into the subscriber namespace (or vice versa) is a
            # mislabelled hazard, not something to guess about.
            raise AssetError(
                f"asset {asset_id!r} declared {kind!r} but URL {url!r} "
                f"classifies as {classified!r}"
            )
    if kind == INLINE and not raw.get("bytes_b64"):
        raise AssetError(f"inline asset {asset_id!r} is missing bytes_b64")
    return AssetRef(
        asset_id=asset_id,
        kind=kind,
        role=raw.get("role", "decor"),
        url=url,
        media_type=raw.get("media_type", ""),
        alt=raw.get("alt", ""),
        bytes_b64=raw.get("bytes_b64", ""),
    )
