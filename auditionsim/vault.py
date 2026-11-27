"""Directory isolation: per-point vaults and the coordinator boundary.

Each audition point keeps its participants' individual scores in its OWN
private store.  The coordinator (and the booth) are modelled as objects that
only hold *interface handles*; they have no method that returns a person's
score.  This file makes that boundary explicit and testable.
"""

from dataclasses import dataclass

from . import crypto_core as cc
from .model import Participant, Point, Receipt, Voucher


class PointVault:
    """Private per-point directory.  Individual scores live here and nowhere
    else.  Only the point itself reads them -- to build shares/masks.  The
    public surface deliberately exposes NO per-person accessor.
    """

    def __init__(self, point: Point):
        self._point = point
        self._participants: list[Participant] = []  # private

    def enroll(self, participant: Participant) -> None:
        assert participant.seat == self._point.seat
        self._participants.append(participant)

    # --- the only thing the point ever publishes is aggregate-shaped ---------
    def _local_sum(self) -> int:
        """Internal only: used by the point to form shares/masks.  Never
        exposed to the coordinator."""
        return cc.fsum(p.score for p in self._participants)

    def _entries(self):
        """Internal only: iterate (participant, score) to build protocol
        material.  Never exposed."""
        return list(self._participants)

    def __len__(self):
        return len(self._participants)

    def __repr__(self):
        # No scores in the repr either -- avoids accidental leakage in logs.
        return f"PointVault({self._point.name}, <{len(self._participants)} private records>)"


class Coordinator:
    """The coordinator may only drive agreed protocol interfaces.  It is
    constructed with handles to points (submit interfaces) and custodians /
    booth, but it never holds a PointVault.  There is intentionally no
    read_participant() / read_scores() on this class.
    """

    def __init__(self, submit_ifaces, custodian_ifaces, booth_iface):
        self._submit = submit_ifaces          # {point_name: SubmitInterface}
        self._custodians = custodian_ifaces
        self._booth = booth_iface

    # The coordinator's whole vocabulary:
    def run_submissions(self, *args, **kwargs):
        return {name: s.submit(*args, **kwargs) for name, s in self._submit.items()}

    def attempt_publish(self, *args, **kwargs):
        return self._booth.attempt_publish(*args, **kwargs)

    def query_receipt(self, *args, **kwargs):
        return self._booth.query_receipt(*args, **kwargs)

    def __repr__(self):
        return "Coordinator(interface-only; no per-person access)"


def assert_coordinator_cannot_read(coord: Coordinator) -> list[str]:
    """Prove the coordinator has no per-person read path.  Returns the list of
    forbidden attribute names that were correctly ABSENT."""
    forbidden = ["read_participant", "read_scores", "scores", "participants",
                 "read_person", "get_score", "vault", "_vault"]
    absent = [f for f in forbidden if not hasattr(coord, f)]
    return absent
