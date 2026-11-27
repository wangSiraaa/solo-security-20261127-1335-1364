"""Import ("seal at arrival") and the sealed local edition.

Strategy chosen: **claim shared public illustrations once, seal a local
edition.**  At issue-seal time we

1. read the boxed issue from the wire,
2. copy every ``inline`` illustration straight out of the letter bytes,
3. request each ``public`` illustration exactly once (deduplicated by path),
4. never touch any ``subscriber`` address,
5. write a self-contained edition directory with local image copies and an
   ``edition.json`` that records, per image, its owner, provenance and whether
   it was substituted.

Once sealed, the edition is what readers browse.  Because per-subscriber
images were not fetched and shared ones were fetched once per issue (not per
reader), the supply-station log for an issue is identical no matter how
readers later page back and forth, print, or close letters.

Ownership, not appearance
--------------------------
Every sealed image is keyed by ``owner::asset_id``.  Two letters may carry a
byte-for-byte identical picture -- one inline, one bound to a subscriber --
and they stay separate assets with separate provenance.  A reader of one
letter can never resolve the other letter's copy, even though it looks the
same.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .assets import INLINE, PUBLIC, SUBSCRIBER, AssetRef
from .wire import WireMailbox

EDITION_FORMAT = "reading-room-edition/1"

#: Owner tag for illustrations shared by the whole issue.
ISSUE_OWNER = "__issue__"


class SealError(RuntimeError):
    """Raised when an edition cannot be sealed safely."""


def asset_key(owner: str, asset_id: str) -> str:
    return f"{owner}::{asset_id}"


@dataclass
class SealedAsset:
    """One image as it exists inside the sealed edition."""

    key: str
    asset_id: str
    owner: str
    kind: str
    role: str
    alt: str
    provenance: str  # "inline" | "public" | "substituted"
    local_file: str  # path inside the edition dir
    sha256: str
    original_url: str = ""
    note: str = ""
    label: str = ""  # shown to the reader when this is a substitute

    @property
    def substituted(self) -> bool:
        return self.provenance == "substituted"


@dataclass
class SealedLetter:
    letter_id: str
    subscriber: str
    title: str
    body_text: str
    arrival_gate: str
    events: list[dict]
    directions: dict
    letter_map: dict | None
    map_description: dict | None
    assets: list[str]  # composite asset keys, in reading order

    def event(self, event_number: str) -> dict:
        for ev in self.events:
            if ev.get("event_number") == event_number:
                return ev
        raise KeyError(event_number)


@dataclass
class Edition:
    edition_id: str
    issue: str
    sealed_at: str
    source: str
    node_labels: dict[str, str]
    letters: list[SealedLetter]
    assets: dict[str, SealedAsset]
    common_map_key: str
    common_map_description: dict
    substitutions: list[dict] = field(default_factory=list)

    # --- lookups -------------------------------------------------------
    def letter(self, letter_id: str) -> SealedLetter:
        for letter in self.letters:
            if letter.letter_id == letter_id:
                return letter
        raise KeyError(letter_id)

    def asset(self, key: str) -> SealedAsset:
        return self.assets[key]

    def local_path(self, key: str) -> Path:
        return Path(self.source) / self.assets[key].local_file

    def visible_assets(self, letter: SealedLetter) -> list[SealedAsset]:
        """Assets a reader of ``letter`` may see (its own + issue-wide)."""
        return [self.assets[key] for key in letter.assets]

    def assert_visible(self, letter: SealedLetter, key: str) -> SealedAsset:
        """Enforce ownership: a reader may only see own or issue-wide images."""
        asset = self.assets[key]
        if asset.owner == ISSUE_OWNER:
            return asset
        if asset.owner != letter.letter_id:
            raise PermissionError(
                f"asset {asset.asset_id!r} belongs to {asset.owner!r}, "
                f"not to letter {letter.letter_id!r}"
            )
        return asset


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------

#: Tiny 1x1 transparent PNG, used when a referenced image could not be sealed.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _placeholder_bytes() -> bytes:
    return _PLACEHOLDER_PNG


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ext_for(media_type: str, default: str = ".png") -> str:
    if media_type:
        guessed = mimetypes.guess_extension(media_type.split(";")[0].strip())
        if guessed:
            return guessed
    return default


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------

class _Fetcher:
    """Fetches only public URLs; deduplicates identical paths within a seal."""

    def __init__(self, base_url: str | None, allow_network: bool):
        self.base_url = (base_url or "").rstrip("/")
        self.allow_network = allow_network
        self._cache: dict[str, bytes | None] = {}
        self.requested_paths: list[str] = []

    def fetch_public(self, asset: AssetRef) -> bytes | None:
        if asset.kind != PUBLIC:
            raise SealError(f"refusing to fetch non-public asset {asset.asset_id!r}")
        path = asset.url
        if path in self._cache:
            return self._cache[path]
        self.requested_paths.append(path)
        if not self.base_url or not self.allow_network:
            self._cache[path] = None
            return None
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=10) as resp:
                status = getattr(resp, "status", 200)
                data = resp.read() if status == 200 else None
        except (urllib.error.URLError, OSError, ValueError):
            data = None
        self._cache[path] = data
        return data


def seal_issue(
    mailbox: WireMailbox,
    edition_dir,
    base_url: str | None = None,
    allow_network: bool = True,
) -> Edition:
    """Seal ``mailbox`` into a self-contained local edition directory."""
    edition_dir = Path(edition_dir)
    assets_dir = edition_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    fetcher = _Fetcher(base_url, allow_network)
    sealed_assets: dict[str, SealedAsset] = {}
    substitutions: list[dict] = []

    def store_bytes(asset, owner, data, provenance, note="", label="") -> SealedAsset:
        ext = _ext_for(asset.media_type)
        local_name = f"{owner.replace('/', '_')}__{asset.asset_id}{ext}"
        local_path = assets_dir / local_name
        local_path.write_bytes(data)
        key = asset_key(owner, asset.asset_id)
        if key in sealed_assets:
            raise SealError(f"duplicate sealed asset {key!r}")
        sealed = SealedAsset(
            key=key,
            asset_id=asset.asset_id,
            owner=owner,
            kind=asset.kind,
            role=asset.role,
            alt=asset.alt,
            provenance=provenance,
            local_file=str(local_path.relative_to(edition_dir)),
            sha256=_sha256(data),
            original_url=asset.url,
            note=note,
            label=label,
        )
        sealed_assets[key] = sealed
        return sealed

    def substitute(asset, owner, reason, label, shown) -> SealedAsset:
        sealed = store_bytes(
            asset, owner, _placeholder_bytes(), "substituted",
            note=reason, label=label,
        )
        substitutions.append({
            "owner": owner,
            "asset_id": asset.asset_id,
            "role": asset.role,
            "reason": reason,
            "original_url": asset.url,
            "shown": shown,
            "label": label,
        })
        return sealed

    def seal_asset(asset: AssetRef, owner: str) -> SealedAsset:
        if asset.kind == INLINE:
            return store_bytes(asset, owner, base64.b64decode(asset.bytes_b64), "inline")
        if asset.kind == PUBLIC:
            data = fetcher.fetch_public(asset)
            if data is None:
                return substitute(
                    asset, owner,
                    reason="public illustration unavailable at seal time",
                    label="（公共插图封存时不可用，已以替代图呈现）",
                    shown="placeholder",
                )
            return store_bytes(asset, owner, data, "public")
        if asset.kind == SUBSCRIBER:
            # Never request it; render a clear, labelled substitute instead.
            return substitute(
                asset, owner,
                reason="per-subscriber address withheld for reader privacy",
                label="（订阅者专属插图：为保护读者，本读本不请求该地址）",
                shown="labelled_placeholder",
            )
        raise SealError(f"cannot seal asset of kind {asset.kind!r}")

    # Shared issue map first.
    common = seal_asset(mailbox.common_map, ISSUE_OWNER)

    sealed_letters: list[SealedLetter] = []
    for letter in mailbox.letters:
        letter_asset_keys = []
        for asset in letter.assets:
            sealed = seal_asset(asset, letter.letter_id)
            letter_asset_keys.append(sealed.key)

        letter_map_dict = None
        map_desc_dict = None
        if letter.letter_map is not None:
            sealed_map = seal_asset(letter.letter_map, letter.letter_id)
            map_desc_dict = _route_to_dict(letter.map_description)
            letter_map_dict = _asset_to_dict(sealed_map, is_map=True)
            letter_map_dict["description"] = map_desc_dict

        sealed_letters.append(
            SealedLetter(
                letter_id=letter.letter_id,
                subscriber=letter.subscriber,
                title=letter.title,
                body_text=letter.body_text,
                arrival_gate=letter.arrival_gate,
                events=[
                    {
                        "event_number": e.event_number,
                        "title": e.title,
                        "venue": e.venue,
                        "venue_node": e.venue_node,
                        "note": e.note,
                    }
                    for e in letter.events
                ],
                directions=_route_to_dict(letter.directions),
                letter_map=letter_map_dict,
                map_description=map_desc_dict,
                assets=letter_asset_keys,
            )
        )

    common_dict = _asset_to_dict(common, is_map=True)
    common_dict["description"] = _route_to_dict(mailbox.common_map_description)

    edition = Edition(
        edition_id=f"{mailbox.issue}@{_now_stamp()}",
        issue=mailbox.issue,
        sealed_at=_now_iso(),
        source=str(edition_dir.resolve()),
        node_labels=dict(mailbox.node_labels),
        letters=sealed_letters,
        assets=sealed_assets,
        common_map_key=common.key,
        common_map_description=_route_to_dict(mailbox.common_map_description),
        substitutions=substitutions,
    )
    write_edition(edition, common_dict)
    return edition


def _asset_to_dict(asset: SealedAsset, is_map: bool = False) -> dict:
    return {
        "key": asset.key,
        "asset_id": asset.asset_id,
        "owner": asset.owner,
        "kind": asset.kind,
        "role": asset.role,
        "alt": asset.alt,
        "provenance": asset.provenance,
        "local_file": asset.local_file,
        "sha256": asset.sha256,
        "original_url": asset.original_url,
        "note": asset.note,
        "label": asset.label,
        "is_map": is_map,
        "substituted": asset.substituted,
    }


def _route_to_dict(desc) -> dict:
    return {
        "label": getattr(desc, "label", ""),
        "edges": [
            {
                "u": e.u,
                "v": e.v,
                "access": e.access,
                "weight": e.weight,
                "one_way": e.one_way,
                "note": e.note,
            }
            for e in desc.edges
        ],
        "notes": list(desc.notes),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_edition(edition: Edition, common_map_dict: dict | None = None) -> Path:
    edition_dir = Path(edition.source)
    edition_dir.mkdir(parents=True, exist_ok=True)
    if common_map_dict is None:
        common_map_dict = {
            "key": edition.common_map_key,
            "description": edition.common_map_description,
        }
    payload = {
        "format": EDITION_FORMAT,
        "edition_id": edition.edition_id,
        "issue": edition.issue,
        "sealed_at": edition.sealed_at,
        "node_labels": edition.node_labels,
        "common_map": common_map_dict,
        "common_map_description": edition.common_map_description,
        "substitutions": edition.substitutions,
        "assets": {key: _asset_to_dict(a) for key, a in edition.assets.items()},
        "letters": [
            {
                "letter_id": l.letter_id,
                "subscriber": l.subscriber,
                "title": l.title,
                "body_text": l.body_text,
                "arrival_gate": l.arrival_gate,
                "events": l.events,
                "directions": l.directions,
                "letter_map": l.letter_map,
                "map_description": l.map_description,
                "assets": l.assets,
            }
            for l in edition.letters
        ],
    }
    path = edition_dir / "edition.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def _sealed_asset_from_dict(a: dict) -> SealedAsset:
    return SealedAsset(
        key=a["key"],
        asset_id=a["asset_id"],
        owner=a["owner"],
        kind=a["kind"],
        role=a["role"],
        alt=a.get("alt", ""),
        provenance=a["provenance"],
        local_file=a["local_file"],
        sha256=a.get("sha256", ""),
        original_url=a.get("original_url", ""),
        note=a.get("note", ""),
        label=a.get("label", ""),
    )


def load_edition(edition_dir) -> Edition:
    edition_dir = Path(edition_dir)
    with open(edition_dir / "edition.json", "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("format") != EDITION_FORMAT:
        raise SealError(f"unsupported edition format {payload.get('format')!r}")

    assets = {key: _sealed_asset_from_dict(a) for key, a in payload["assets"].items()}
    letters = [
        SealedLetter(
            letter_id=l["letter_id"],
            subscriber=l.get("subscriber", ""),
            title=l.get("title", ""),
            body_text=l.get("body_text", ""),
            arrival_gate=l.get("arrival_gate", "gate"),
            events=l.get("events", []),
            directions=l.get("directions", {"edges": [], "notes": []}),
            letter_map=l.get("letter_map"),
            map_description=l.get("map_description"),
            assets=l.get("assets", []),
        )
        for l in payload["letters"]
    ]
    return Edition(
        edition_id=payload["edition_id"],
        issue=payload["issue"],
        sealed_at=payload["sealed_at"],
        source=str(edition_dir.resolve()),
        node_labels=payload.get("node_labels", {}),
        letters=letters,
        assets=assets,
        common_map_key=payload["common_map"]["key"],
        common_map_description=payload.get("common_map_description", {}),
        substitutions=payload.get("substitutions", []),
    )
