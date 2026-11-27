"""Privacy-preserving reading-room statistics.

A small, self-contained exercise implementation of:

* a *mock* local privacy budget that is charged per affected household,
  once per distinct published release;
* a query-desk that signs a release, exposes it exactly once, and refunds
  the charge on a lost acknowledgement so that an honest retry reuses the
  very same perturbation instead of sampling a fresh one;
* a read-only display board that can only show *received* exhibits and can
  always be rebuilt from stored release magnitudes alone (no household
  ledger reaches the board);
* a trend statement that refuses to call two overlapping exhibits two
  *independent* observations, even though both may be genuine releases.

The perturbation algorithm in ``core.Mechanism`` is a deterministic toy.  It
is **not** a real privacy mechanism and nothing here estimates privacy
strength from samples; the budget arithmetic is the point.
"""

from .core import Budget, Mechanism, ReleaseRecord

__all__ = ["Budget", "Mechanism", "ReleaseRecord"]
