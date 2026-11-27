"""Local simulated wireless-channel round-trip witnesses."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Dict

from .clockutil import iso, now
from .errors import ValidationError
from .security import sign_token, verify_token, write_private_json
from .store import Store


def perform_roundtrip(store: Store, channel: str, out: str | Path | None) -> Dict[str, Any]:
    if not channel or not isinstance(channel, str):
        raise ValidationError("a channel name is required")
    config = store.config()
    key = store.key()
    issued_at = now()
    expires_at = issued_at + int(config["witness_ttl_seconds"])
    nonce = secrets.token_hex(16)

    # In this local simulation the channel authority and gateway share the
    # local HMAC key.  The signed witness is what the gateway actually trusts;
    # a copied display name or egress label is never enough.
    witness = sign_token(
        {
            "typ": "witness",
            "channel": channel,
            "nonce": nonce,
            "iat": issued_at,
            "exp": expires_at,
        },
        key,
    )
    report = {
        "schema": "anon-station-roundtrip/1",
        "channel": channel,
        "nonce": nonce,
        "observed_at": iso(issued_at),
        "witness_expires_at": expires_at,
        "witness": witness,
    }
    report_path = None
    if out:
        report_path = write_private_json(out, report)
    return {
        "ok": True,
        "channel": channel,
        "nonce": nonce,
        "observed_at": report["observed_at"],
        "witness_expires_at": expires_at,
        "report_path": str(report_path) if report_path else None,
        "report": report if report_path is None else None,
    }


def load_verified_witness(store: Store, report_path: str | Path) -> Dict[str, Any]:
    path = Path(report_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"witness report not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"witness report is not JSON: {exc}") from exc

    key = store.key()
    payload = verify_token(report.get("witness"), key, expected_type="witness")

    # The outer report is convenient for operators and tampering demos, but it
    # is not authoritative.  Reject a display field edited without the signed
    # witness, and also reject a valid witness for a different channel when the
    # caller presents it as this report.
    if report.get("channel") != payload.get("channel"):
        from .errors import ReportMismatchError

        raise ReportMismatchError(
            "report channel does not match signed round-trip witness",
            report_channel=report.get("channel"),
            witness_channel=payload.get("channel"),
        )
    if report.get("nonce") != payload.get("nonce"):
        from .errors import ReportMismatchError

        raise ReportMismatchError("report nonce does not match signed witness")
    return payload
