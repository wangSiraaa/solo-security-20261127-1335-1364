"""Museum referral / acceptance drill -- an offline, reproducible exercise.

Run ``python -m museum.demo`` for the full scenario, or import the pieces to
build your own.  See README.md for the requirement-to-test map.
"""

from .crypto import Key, canonical_json, sha256_hex, verify, verify_cached
from .model import (
    ALL_PURPOSES,
    CLAIM_EXHIBIT,
    DOC_PURPOSE,
    VERIFY_DESCRIPTION,
    CollectionScope,
    Document,
    Item,
    OneTimeAcceptanceMaterial,
    Referral,
    Registration,
    Validity,
    format_call_number,
    iso,
    parse_prefix,
    parse_time,
)
from .issue import Librarian, Museum, now
from .authority import (
    Decision,
    Path,
    ReferralGraph,
    TranslationDesk,
    TranslationStatement,
    authorize_stepwise,
    authorize_via_desk,
)
from .receiver import (
    Dossier,
    LocalLedger,
    Receiver,
    ROUTE_DESK,
    ROUTE_STEP,
)

__all__ = [
    "Key", "canonical_json", "sha256_hex", "verify", "verify_cached",
    "ALL_PURPOSES", "CLAIM_EXHIBIT", "VERIFY_DESCRIPTION", "DOC_PURPOSE",
    "CollectionScope", "Document", "Item", "OneTimeAcceptanceMaterial",
    "Referral", "Registration", "Validity", "format_call_number", "iso", "now",
    "parse_prefix", "parse_time",
    "Librarian", "Museum", "Decision", "Path", "ReferralGraph",
    "TranslationDesk", "TranslationStatement", "authorize_stepwise",
    "authorize_via_desk", "Dossier", "LocalLedger", "Receiver",
    "ROUTE_DESK", "ROUTE_STEP",
]
