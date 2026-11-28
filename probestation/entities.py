"""Probe identities, factory material, and per-exercise attestations.

Each probe answers a *named* roll-call only if the request carries the
current session nonce.  The attestation that the management line holds only
proves "some probe with this identity answered this session's roll-call"; it
intentionally carries *no slot name*.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Dict, Optional

from .crypto import key_fingerprint, mac, verify_mac

# Distinct, human-meaningful factory identities (never slot-order-like).
PROBE_IDS = ("P-A1", "P-B2", "P-C3", "P-D4", "P-E5", "P-F6")


@dataclass(frozen=True)
class Attestation:
    """Management-line attestation for one probe for one exercise.

    Binds identity -> exercised factory fingerprint, management signature over
    it, but deliberately *not* any physical slot.
    """

    probe_id: str
    fingerprint: str
    mgmt_tag: str  # management signature over {probe_id, fingerprint, exercise}

    def as_dict(self) -> dict:
        return {
            "probe_id": self.probe_id,
            "fingerprint": self.fingerprint,
            "mgmt_tag": self.mgmt_tag,
        }


class Probe:
    """A probe with factory identity material and its own challenge logic."""

    def __init__(self, probe_id: str, factory_secret: Optional[bytes] = None):
        self.probe_id = probe_id
        self._secret = factory_secret if factory_secret is not None else secrets.token_bytes(32)
        self.attestation: Optional[Attestation] = None

    @property
    def fingerprint(self) -> str:
        return key_fingerprint(self._secret)

    # ---- management line provisions the per-exercise attestation ----------
    def enroll(self, mgmt_key: bytes, exercise: str) -> Attestation:
        body = {
            "probe_id": self.probe_id,
            "fingerprint": self.fingerprint,
            "exercise": exercise,
        }
        self.attestation = Attestation(
            probe_id=self.probe_id,
            fingerprint=self.fingerprint,
            mgmt_tag=mac(mgmt_key, body),
        )
        return self.attestation

    # ---- answer a named roll-call -----------------------------------------------
    def answer(self, nonce: str, challenge_id: str) -> Optional[dict]:
        """Return a receipt iff this probe is named and the nonce is current.

        The probe signs ``(probe_id, nonce, challenge_id)`` with its own factory
        secret; the management line can forward the receipt but cannot mint a
        new one.  There is no slot information in the answer.
        """
        if self.attestation is None:
            return None
        body = {
            "probe_id": self.probe_id,
            "nonce": nonce,
            "challenge_id": challenge_id,
        }
        return {
            "probe_id": self.probe_id,
            "nonce": nonce,
            "challenge_id": challenge_id,
            "tag": mac(self._secret, body),
            "fingerprint": self.fingerprint,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Probe({self.probe_id!r}, fp={self.fingerprint})"


class ProbeRegistry:
    """Factory identity book: ids, public fingerprints, attestations only.

    This is the *identity* register.  It never stores a slot number; that
    relationship exists only in the physical rig.
    """

    def __init__(self, probe_ids=PROBE_IDS):
        self._probes: Dict[str, Probe] = {pid: Probe(pid) for pid in probe_ids}
        self._mgmt_key = secrets.token_bytes(32)
        self._exercise: Optional[str] = None
        self._attestations: Dict[str, Attestation] = {}

    # provisioning ---------------------------------------------------------
    def start_exercise(self, exercise: str) -> None:
        self._exercise = exercise
        self._attestations = {}
        for p in self._probes.values():
            self._attestations[p.probe_id] = p.enroll(self._mgmt_key, exercise)

    @property
    def exercise(self) -> Optional[str]:
        return self._exercise

    def probe(self, probe_id: str) -> Probe:
        return self._probes[probe_id]

    @property
    def probe_ids(self):
        return tuple(self._probes)

    def attestation(self, probe_id: str) -> Attestation:
        return self._attestations[probe_id]

    # verification helpers used by the verifier ---------------------------
    def verify_attestation(self, att: dict) -> bool:
        """Check a *received* attestation dict against the management register."""
        if self._exercise is None:
            return False
        pid = att.get("probe_id")
        known = self._attestations.get(pid)
        if known is None:
            return False
        body = {
            "probe_id": pid,
            "fingerprint": att.get("fingerprint"),
            "exercise": self._exercise,
        }
        # Bind received fingerprint to the register (attestation is per-exercise).
        if not verify_mac(self._mgmt_key, body, att.get("mgmt_tag", "")):
            return False
        return att.get("fingerprint") == known.fingerprint

    def verify_answer(self, receipt: dict, nonce: str, challenge_id: str) -> bool:
        """Fully verify a probe's roll-call receipt for *this* session."""
        pid = receipt.get("probe_id")
        if pid not in self._probes:
            return False
        if receipt.get("nonce") != nonce or receipt.get("challenge_id") != challenge_id:
            return False
        att_known = self._attestations.get(pid)
        if att_known is None:
            return False
        if receipt.get("fingerprint") != att_known.fingerprint:
            return False
        body = {
            "probe_id": pid,
            "nonce": nonce,
            "challenge_id": challenge_id,
        }
        return verify_mac(self._probes[pid]._secret, body, receipt.get("tag", ""))
