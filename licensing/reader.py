"""Reader-facing capability boundary.

"The installed unit is a toy trusted player; ordinary readers may only call the
play, return, and transfer (transfer-to-another-machine / '转机') interfaces."

This facade deliberately exposes EXACTLY those three verbs and nothing else.
It does not expose seal(), receive(), prefetch(), return_proof() or any desk
internals. Internally:

    play(...)   -> may pause/resume/open/live, but never lets a preview-only
                   segment be cached, and never spends past the credit count.
    return_(...) -> seals the device, then either settles online (signed proof)
                   or asks the desk for an honest PENDING / LIMITED receipt.
    transfer(...) -> seals this device and asks the desk to carry the remaining
                   credits to another machine (only legal once sealed).
"""
from __future__ import annotations

from typing import List, Optional

from .desk import Desk, DeskResult
from .player import Player


class ReaderCapability:
    """The three verbs an ordinary reader is allowed to call."""

    def __init__(self, player: Player, desk: Desk) -> None:
        self._player = player
        self._desk = desk

    # ------------------------------------------------------------------ play
    def play(self, lease_id: str, seg_id: Optional[str] = None) -> bool:
        """Play a segment. With no seg_id, resumes a paused mid-segment."""
        if seg_id is None:
            return self._player.resume(lease_id)
        st = self._player.states[lease_id]
        seg = self._player._seg(st, seg_id)
        if seg is not None and seg.kind.value == "preview-only":
            return self._player.play_live(lease_id, seg_id)
        return self._player.open(lease_id, seg_id)

    def pause(self, lease_id: str) -> bool:
        # Pause is part of "play" from the reader's point of view.
        return self._player.pause(lease_id)

    # ---------------------------------------------------------------- return
    def return_(
        self,
        lease_id: str,
        show_counter: bool = True,
    ) -> DeskResult:
        """Seal, then return.

        Online  -> settle with a signed proof (exact).
        Offline -> the desk gets ONLY what the player itself can show:
                   * show_counter=False -> bare PENDING / "to be checked".
                   * show_counter=True  -> the device's *own* frozen sealed
                     counter, which yields a LIMITED provisional release.

        The consumed count is deliberately taken from the player's sealed
        counter (device-sourced), NEVER from a caller-supplied number, so a
        reader cannot under-report to over-release credits.
        """
        p = self._player
        p.seal(lease_id)
        if p.online:
            proof = p.return_proof(lease_id)
            return self._desk.settle_return(proof)

        rep = p.sealed_report(lease_id)
        return self._desk.provisional_return(
            lease_id,
            online=False,
            sealed_report=rep["sealed"],
            reported_consumed=rep["plays_consumed"] if show_counter else None,
        )

    # -------------------------------------------------------------- transfer
    def transfer(
        self,
        lease_id: str,
        to_device_id: str,
        new_borrower_id: str,
        ttl: int = 3600,
    ) -> DeskResult:
        """Seal this device and carry its remaining credits to another machine."""
        p = self._player
        p.seal(lease_id)
        proof = p.return_proof(lease_id)
        return self._desk.transfer_lease(
            proof, to_device_id=to_device_id,
            new_borrower_id=new_borrower_id, ttl=ttl,
        )

    # ------------------------------------------------------------- read-only
    def my_lines(self) -> List[str]:
        return self._player.lines()
