"""Approach B -- a trusted verifier that sees the details and issues the station
only a *signed isolation proof*.

The domains hand their real segment/adjacency detail to the **trusted verifier**
(受托核验处).  The station never sees that internal structure.  It receives an
``IsolationProof`` that:

* states the two named leases are disjoint over every fault domain,
* is **bound to the two actually-installed concrete paths** by a commitment to
  their resolved port-level detail (so the ports must match what the station
  really plugged), and to the ingress/egress access ports,
* carries an epoch / contract window,
* is signed by the verifier's key.  The station holds only the verify key.

Consequences the exercise relies on:

* The station can verify the signature, that the bound ports equal the ports it
  physically attached, that the proof is fresh for this epoch and inside the
  contract window, and that the proof's negative claim ("disjoint") holds.
* It CANNOT see which domain the two leases might share (the internal relation
  stays hidden); a forged/guessed "shared" claim means nothing.
* After a segment is re-leased, the path commitment changes.  The old proof no
  longer binds the installed path, and the station cannot mint a replacement by
  guessing -- only the verifier can sign a fresh one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .crypto import H
from .model import Dispatcher
from .signing import ToySigner, verify as rsa_verify
from .world import World


@dataclass(frozen=True)
class IsolationProof:
    proof_id: str
    epoch: int
    lease_a: str
    lease_b: str
    ingress_a: str
    egress_a: str
    ingress_b: str
    egress_b: str
    path_commit_a: str  # commitment binding concrete path A
    path_commit_b: str
    disjoint: bool
    valid_from: int
    valid_until: int
    verifier_pub: Tuple[int, int, int]
    signature: int

    def statement(self) -> dict:
        """Exactly the bytes the verifier signs (signature/pub excluded)."""
        return {
            "proof_id": self.proof_id,
            "epoch": self.epoch,
            "lease_a": self.lease_a,
            "lease_b": self.lease_b,
            "ingress_a": self.ingress_a,
            "egress_a": self.egress_a,
            "ingress_b": self.ingress_b,
            "egress_b": self.egress_b,
            "path_commit_a": self.path_commit_a,
            "path_commit_b": self.path_commit_b,
            "disjoint": self.disjoint,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
        }


@dataclass
class Submission:
    """A domain's private handover to the verifier (never shown to the station)."""

    domain: str
    leases: Dict[str, dict]


class TrustedVerifier:
    """The verifier.  Holds the signing key and the real path detail."""

    def __init__(self, world: World, signer: Optional[ToySigner] = None) -> None:
        self.world = world
        self.dispatcher: Dispatcher = world.dispatcher
        self.signer = signer or ToySigner()
        self.submissions: Dict[str, Submission] = {}
        self.issued: List[IsolationProof] = []
        self._pub = self.signer.public()

    def public_key(self) -> Tuple[int, int, int]:
        return self._pub

    # ---- import domain detail ----------------------------------------

    def receive(self, domain: str, lease_ids: List[str]) -> None:
        """A domain hands over the path detail it holds for these leases."""
        detail: Dict[str, dict] = {}
        for lid in lease_ids:
            lease = self.world.leases[lid]
            view = self.dispatcher.resolve(lease)
            # Only include what this domain touches; here we take the whole path
            # for simplicity but keep it inside the verifier.
            detail[lid] = {
                "hops": [list(h.key()) for h in lease.hops],
                "detail": [list(d) for d in view.detail],
                "ingress": view.ingress,
                "egress": view.egress,
                "interchanges": list(view.interchanges),
                "domains": sorted(view.domains),
                "valid_from": lease.valid_from,
                "valid_until": lease.valid_until,
            }
        self.submissions[domain] = Submission(domain=domain, leases=detail)

    def refresh(self, lease_ids: List[str], domains: Optional[List[str]] = None) -> None:
        """Re-read real path detail from the world (after a topology change)."""
        for domain in (domains or list(self.submissions.keys())):
            self.receive(domain, lease_ids)

    # ---- internals ----------------------------------------------------

    def _path_commit(self, lease_id: str) -> str:
        lease = self.world.leases[lease_id]
        view = self.dispatcher.resolve(lease)
        return H({"path": view.commit_payload()})

    def _lookup(self, lease_id: str) -> dict:
        for sub in self.submissions.values():
            if lease_id in sub.leases:
                return sub.leases[lease_id]
        raise KeyError(f"verifier has no detail for {lease_id}")

    # ---- issuance -----------------------------------------------------

    def issue(self, lease_a: str, lease_b: str, epoch: Optional[int] = None) -> IsolationProof:
        """Compute disjointness from real detail and sign a fresh proof."""
        epoch = self.world.epoch if epoch is None else epoch
        la = self._lookup(lease_a)
        lb = self._lookup(lease_b)

        dom_a = set(la["domains"])
        dom_b = set(lb["domains"])
        disjoint = dom_a.isdisjoint(dom_b)

        valid_from = min(la["valid_from"], lb["valid_from"])
        valid_until = min(la["valid_until"], lb["valid_until"])

        stmt = {
            "proof_id": H({"a": lease_a, "b": lease_b, "epoch": epoch,
                           "ca": self._path_commit(lease_a),
                           "cb": self._path_commit(lease_b)}),
            "epoch": epoch,
            "lease_a": lease_a,
            "lease_b": lease_b,
            "ingress_a": la["ingress"],
            "egress_a": la["egress"],
            "ingress_b": lb["ingress"],
            "egress_b": lb["egress"],
            "path_commit_a": self._path_commit(lease_a),
            "path_commit_b": self._path_commit(lease_b),
            "disjoint": disjoint,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
        sig = self.signer.sign(_canon(stmt))
        proof = IsolationProof(**stmt, verifier_pub=self._pub, signature=sig)
        self.issued.append(proof)
        return proof


def _canon(obj) -> bytes:
    from .crypto import canonical
    return canonical(obj)


class ProofStation:
    """The liaison station in approach B: only holds the verifier's public key
    and the proofs it was given.  Can verify; cannot forge or renew."""

    def __init__(self, verifier_pub: Tuple[int, int, int]) -> None:
        self.verifier_pub = verifier_pub

    def _signature_ok(self, proof: IsolationProof) -> bool:
        if proof.verifier_pub != self.verifier_pub:
            return False
        return rsa_verify(_canon(proof.statement()), proof.signature, self.verifier_pub)

    def verify_structural(self, proof: IsolationProof) -> Tuple[bool, str]:
        if not self._signature_ok(proof):
            return False, "bad verifier signature"
        if not proof.disjoint:
            return False, "proof asserts the paths are NOT disjoint"
        return True, "signed disjointness ok"

    def binds_actual_paths(
        self,
        proof: IsolationProof,
        attached_src_a: str,
        attached_dst_a: str,
        attached_src_b: str,
        attached_dst_b: str,
    ) -> Tuple[bool, str]:
        """The proof must bind the exact access ports the station attached.

        A detached backup port makes the bound destination port differ from the
        actually-attached one (there is none), so this fails even with a
        perfectly valid signature.
        """
        if proof.ingress_a != attached_src_a or proof.egress_a != attached_dst_a:
            return False, "proof does not bind lease A's actual connected ports"
        if proof.ingress_b != attached_src_b or proof.egress_b != attached_dst_b:
            return False, "proof does not bind lease B's actual connected ports"
        return True, "proof binds the actual connected ports"

    def fresh_for_epoch(self, proof: IsolationProof, epoch: int) -> Tuple[bool, str]:
        if proof.epoch != epoch:
            return False, f"proof is for epoch {proof.epoch}, current is {epoch}"
        return True, "fresh"

    def contract_active(self, proof: IsolationProof, epoch: int) -> Tuple[bool, str]:
        if not (proof.valid_from <= epoch <= proof.valid_until):
            return False, "contract window does not cover current epoch"
        return True, "contract active"

    def current_path_commit(self, lease_id: str) -> Optional[str]:
        """The station does NOT hold this; kept only to show it cannot compute it.

        Raises to make the capability gap explicit in the exercise.
        """
        raise PermissionError(
            "station has no access to internal path detail; cannot recompute "
            "a path commitment or self-renew a proof"
        )
