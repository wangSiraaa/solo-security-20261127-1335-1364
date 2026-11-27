"""Local test adapter standing in for a sealed random/HSM source.

The service trusts *signed provenance records*, not the fact that a string
"looks random".  The HMAC key is deliberately a local development key; a real
deployment would replace this class with an HSM/sealed-source adapter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

LOCAL_TEST_KEY = b"local-test-sealed-source-adapter-key-v1"
VERIFIER_KEY = b"local-coupon-secret-verifier-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_payload(payload: dict[str, Any], key: bytes = LOCAL_TEST_KEY) -> str:
    return hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()


def verify_envelope(envelope: dict[str, Any], key: bytes = LOCAL_TEST_KEY) -> bool:
    if not isinstance(envelope, dict):
        return False
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return False
    expected = sign_payload(payload, key)
    return hmac.compare_digest(expected, signature)


def make_envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {"kind": kind, "issued_at": utc_now(), **payload}
    return {
        "adapter": "local-test-sealed-source",
        "kind": kind,
        "payload": body,
        "signature": sign_payload(body),
    }


def fingerprint_secret(secret: str) -> str:
    """Return the only form of a scratch code persisted by the service."""
    return hmac.new(
        VERIFIER_KEY, secret.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class DrawnCode:
    seq: int
    ticket_no: str
    secret: str
    secret_hash: str


class LocalSealedSourceAdapter:
    """Deterministic-under-testing sealed source adapter.

    Supplying ``seed`` makes demos/tests reproducible.  Production callers omit
    it and receive an OS-seeded independent source.
    """

    name = "local-test-sealed-source"

    def __init__(self, key: bytes = LOCAL_TEST_KEY) -> None:
        self.key = key

    def _sign(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"kind": kind, "issued_at": utc_now(), **payload}
        sig = hmac.new(self.key, canonical_json(body), hashlib.sha256).hexdigest()
        return {"adapter": self.name, "kind": kind, "payload": body, "signature": sig}

    def create_backup(
        self, backup_id: str, *, seed: str | None = None, segment_id: str | None = None
    ) -> dict[str, Any]:
        seed = seed or secrets.token_hex(24)
        state_secret = hmac.new(
            self.key, b"backup-state" + seed.encode(), hashlib.sha256
        ).hexdigest()
        segment_id = segment_id or "SEG-" + secrets.token_hex(4).upper()
        envelope = self._sign(
            "backup_image",
            {
                "backup_id": backup_id,
                "segment_id": segment_id,
                "state_fingerprint": hashlib.sha256(
                    state_secret.encode()
                ).hexdigest()[:16],
            },
        )
        return {
            "backup_id": backup_id,
            "segment_id": segment_id,
            "state_secret": state_secret,
            "envelope": envelope,
        }

    def checkout_source(
        self,
        *,
        machine_id: str,
        source_id: str,
        segment_id: str,
        kind: str,
        state_secret: str,
        epoch: int = 1,
        backup_id: str | None = None,
        independent: bool = False,
        start_seq: int = 0,
    ) -> dict[str, Any]:
        envelope = self._sign(
            "source_checkout",
            {
                "source_id": source_id,
                "machine_id": machine_id,
                "segment_id": segment_id,
                "source_kind": kind,
                "backup_id": backup_id,
                "independent": independent,
                "epoch": epoch,
                "next_seq": start_seq,
            },
        )
        return envelope

    def new_independent_source(
        self,
        *,
        machine_id: str,
        source_id: str | None = None,
        segment_id: str | None = None,
        seed: str | None = None,
    ) -> dict[str, Any]:
        source_id = source_id or "SRC-" + secrets.token_hex(4).upper()
        segment_id = segment_id or "SEG-NEW-" + secrets.token_hex(4).upper()
        seed = seed or secrets.token_hex(24)
        state_secret = hmac.new(
            self.key, b"independent-source" + seed.encode(), hashlib.sha256
        ).hexdigest()
        envelope = self.checkout_source(
            machine_id=machine_id,
            source_id=source_id,
            segment_id=segment_id,
            kind="independent_seed",
            state_secret=state_secret,
            independent=True,
        )
        return {
            "source_id": source_id,
            "segment_id": segment_id,
            "state_secret": state_secret,
            "epoch": 1,
            "next_seq": 0,
            "envelope": envelope,
        }

    def restore_from_backup(
        self,
        *,
        machine_id: str,
        backup_id: str,
        segment_id: str,
        state_secret: str,
        source_id: str | None = None,
        start_seq: int = 0,
    ) -> dict[str, Any]:
        source_id = source_id or "SRC-" + secrets.token_hex(4).upper()
        envelope = self.checkout_source(
            machine_id=machine_id,
            source_id=source_id,
            segment_id=segment_id,
            kind="restored_backup",
            state_secret=state_secret,
            backup_id=backup_id,
            independent=False,
            start_seq=start_seq,
        )
        return {
            "source_id": source_id,
            "segment_id": segment_id,
            "state_secret": state_secret,
            "epoch": 1,
            "next_seq": start_seq,
            "envelope": envelope,
        }

    def reseed(
        self,
        *,
        source_id: str,
        machine_id: str,
        segment_id: str,
        old_epoch: int,
        old_state_secret: str,
        new_seed: str | None = None,
    ) -> dict[str, Any]:
        new_seed = new_seed or secrets.token_hex(24)
        new_state_secret = hmac.new(
            self.key,
            b"reseed" + old_state_secret.encode() + new_seed.encode(),
            hashlib.sha256,
        ).hexdigest()
        new_epoch = old_epoch + 1
        envelope = self._sign(
            "reseed",
            {
                "source_id": source_id,
                "machine_id": machine_id,
                "segment_id": segment_id,
                "old_epoch": old_epoch,
                "new_epoch": new_epoch,
                "new_state_fingerprint": hashlib.sha256(
                    new_state_secret.encode()
                ).hexdigest()[:16],
            },
        )
        return {
            "state_secret": new_state_secret,
            "epoch": new_epoch,
            "next_seq": 0,
            "envelope": envelope,
        }

    def draw(
        self,
        *,
        state_secret: str,
        epoch: int,
        start_seq: int,
        ticket_nos: Iterable[str],
    ) -> list[DrawnCode]:
        drawn: list[DrawnCode] = []
        for offset, ticket_no in enumerate(ticket_nos):
            seq = start_seq + offset
            raw = hmac.new(
                state_secret.encode("utf-8"),
                f"{epoch}:{seq}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            # Human-readable scratch code; entropy/provenance is not inferred
            # from this formatting by the service.
            secret = "SCR-" + "-".join(raw[i : i + 4].upper() for i in range(0, 16, 4))
            drawn.append(
                DrawnCode(
                    seq=seq,
                    ticket_no=ticket_no,
                    secret=secret,
                    secret_hash=fingerprint_secret(secret),
                )
            )
        return drawn

    def usage_witness(
        self,
        *,
        witness_id: str,
        source_id: str,
        machine_id: str,
        segment_id: str,
        epoch: int,
        drawn: list[DrawnCode],
        previous_chain_hash: str | None = None,
    ) -> dict[str, Any]:
        if not drawn:
            raise ValueError("usage witness requires at least one drawn code")
        items = [
            {
                "seq": item.seq,
                "ticket_no": item.ticket_no,
                "secret_hash": item.secret_hash,
            }
            for item in drawn
        ]
        from_seq = drawn[0].seq
        to_seq = drawn[-1].seq
        chain_input = canonical_json(
            {
                "source_id": source_id,
                "machine_id": machine_id,
                "segment_id": segment_id,
                "epoch": epoch,
                "from_seq": from_seq,
                "to_seq": to_seq,
                "items": items,
                "previous_chain_hash": previous_chain_hash,
            }
        )
        chain_hash = hashlib.sha256(chain_input).hexdigest()
        return self._sign(
            "usage_witness",
            {
                "witness_id": witness_id,
                "source_id": source_id,
                "machine_id": machine_id,
                "segment_id": segment_id,
                "epoch": epoch,
                "from_seq": from_seq,
                "to_seq": to_seq,
                "items": items,
                "previous_chain_hash": previous_chain_hash,
                "chain_hash": chain_hash,
                "continuous": to_seq - from_seq + 1 == len(items),
            },
        )

    def verify(self, envelope: dict[str, Any]) -> bool:
        return verify_envelope(envelope, self.key)
