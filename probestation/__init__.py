"""Probe certification station and independent sampling bench (local simulation).

This package models, with no real hardware, the exercise described in the
task:

* Six sampling probes, each with factory identity material (an asymmetric
  key pair) and a per-exercise attestation signed by the management line.
* A calibration room that issues a *fresh* nonce-bearing roll call, collects
  batched (parallel) signed answers, and reconstructs, with proof, which
  probe identity must physically sit in which slot.
* Two independent solving strategies:
    - ``preplan`` : the whole parallel interrogation schedule is fixed before
      entering the closed rig.  Nothing may be appended inside.
    - ``rollcall``: an on-site roll-caller; after each completed beat (real
      contact pattern plus trusted receipt) it may send one more named probe
      question to whatever is still ambiguous, spending remaining beats where
      reads went missing.
* A rig that records per-slot contact patterns only.  Missing reads are
  explicitly marked.  There is *no* lookup table mapping identity <-> slot
  anywhere.
* A calibration component applying per-probe piecewise calibration curves to
  the integers read that day, emitting a report whose provenance can be
  re-verified.

Security model (all local, fictional hardware):

* ``ContactBus.wire_matrix`` is the simulated internal wiring.  It is used
  *only* by ``Rig.stimulate`` to make contacts actually light up.  The
  certification code (``reconstruct`` / ``preplan`` / ``rollcall``) receives
  only the recorded ``BeatRecord`` objects and must never read the matrix.
* Probes answer named roll-calls only when the request carries the current
  session nonce.  A delayed / stale receipt is worthless.
* All receipts are signature-checked against the factory attestation
  material; a forged or replayed receipt does not establish anything.

Nothing here talks to real hardware; everything is deterministic given a
seed and uses only the standard library (HMAC-SHA256 as the message
authentication primitive).
"""
