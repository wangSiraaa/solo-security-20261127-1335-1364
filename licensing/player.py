"""A toy trusted player.

Model (chosen policy): offline limited-count capability with a non-rollback
counter, PLUS per-segment content provisioning.

Two independent things happen:
  * PROVISIONING  = content arriving for a NORMAL segment (only while online).
                    Provisioned text is cached on the device. This costs no
                    listen credit.
  * CONSUMING     = opening/playing a segment. This spends ONE listen credit on
                    the non-rollback counter, and can be done offline for any
                    segment that was already provisioned.

Consequence, exactly as required:
  * Pre-fetch the whole chapter while connected -> finish a chapter offline.
  * Pre-fetch only part -> on disconnect you can replay only the provisioned
    segments; later never-issued segments honestly show as MISSING.

The player is "trusted" in the toy model: it will not roll the counter back and
will seal itself before emitting a return proof. Preview-only segments are
NEVER provisioned/cached and can only be played live while online.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .clock import SimClock
from .content import SegKind
from .content import chapter as get_chapter
from .crypto import device_secret, mac
from .model import (
    DeviceLeaseState,
    EventKind,
    Lease,
    LeaseScope,
    LeaseState,
    PlayEvent,
    RESUME_WINDOW,
    ReturnProof,
)


class Player:
    def __init__(self, device_id: str, clock: SimClock) -> None:
        self.device_id = device_id
        self.clock = clock
        self.states: Dict[str, DeviceLeaseState] = {}
        self.online: bool = False
        self._line_sink: List[str] = []

    # ------------------------------------------------------------- plumbing
    def lines(self) -> List[str]:
        out = self._line_sink
        self._line_sink = []
        return out

    def _emit(self, line: str) -> None:
        self._line_sink.append(f"[{self.device_id}] {line}")

    def _state(self, lease_id: str) -> Optional[DeviceLeaseState]:
        return self.states.get(lease_id)

    def _seg(self, st: DeviceLeaseState, seg_id: str):
        ch = get_chapter(st.lease.chapter_id)
        return next((s for s in ch.segments if s.seg_id == seg_id), None)

    def bind(self, lease: Lease) -> str:
        if lease.device_id != self.device_id:
            raise ValueError(
                f"lease {lease.lease_id} is bound to {lease.device_id}, not "
                f"{self.device_id}"
            )
        self.states[lease.lease_id] = DeviceLeaseState(lease=lease)
        self._emit(
            f"accepted lease {lease.lease_id}: chapter={lease.chapter_id} "
            f"plays={lease.plays_total} scope={lease.scope.value}"
        )
        return lease.lease_id

    # ---------------------------------------------------------- provisioning
    def receive(self, lease_id: str, seg_id: str) -> bool:
        """Pull one NORMAL segment's content while online. No credit spent."""
        st = self._state(lease_id)
        if st is None:
            raise ValueError("unknown lease")
        if not self.online:
            self._emit(f"cannot receive {seg_id}: player offline")
            return False
        if st.sealed or st.state != LeaseState.ACTIVE:
            self._emit(f"cannot receive {seg_id}: lease sealed/revoked")
            return False
        if self._expired(st):
            self._emit(f"cannot receive {seg_id}: lease expired")
            return False
        if st.lease.scope != LeaseScope.FULL:
            self._emit(f"cannot receive {seg_id}: scope {st.lease.scope.value} is preview-only")
            return False
        seg = self._seg(st, seg_id)
        if seg is None or seg.kind != SegKind.NORMAL:
            self._emit(f"cannot receive {seg_id}: not a cacheable normal segment")
            return False
        st.cache[seg.seg_id] = seg.text
        self._emit(f"received {seg_id} (provisioned, no credit spent)")
        return True

    def prefetch(self, lease_id: str) -> List[str]:
        """Provision every NORMAL segment of the chapter while online."""
        st = self._state(lease_id)
        if st is None:
            return []
        ch = get_chapter(st.lease.chapter_id)
        got = []
        for s in ch.segments:
            if s.kind == SegKind.NORMAL and self.receive(lease_id, s.seg_id):
                got.append(s.seg_id)
        return got

    def receive_until(self, lease_id: str, count: int) -> List[str]:
        """Provision only the first `count` NORMAL segments (partial pre-fetch)."""
        st = self._state(lease_id)
        if st is None:
            return []
        ch = get_chapter(st.lease.chapter_id)
        normals = [s for s in ch.segments if s.kind == SegKind.NORMAL]
        got = []
        for s in normals[:count]:
            if self.receive(lease_id, s.seg_id):
                got.append(s.seg_id)
        return got

    # -------------------------------------------------------------- playback
    def _expired(self, st: DeviceLeaseState) -> bool:
        return self.clock.now() >= st.lease.expires_at

    def open(self, lease_id: str, seg_id: str) -> bool:
        st = self._state(lease_id)
        if st is None:
            raise ValueError("unknown lease")
        seg = self._seg(st, seg_id)
        if seg is None:
            self._refuse(st, seg_id, "not in chapter")
            return False
        if seg.kind == SegKind.PREVIEW_ONLY:
            self._refuse(st, seg_id, "preview-only: use play_live while connected")
            return False
        if st.state != LeaseState.ACTIVE or st.sealed:
            self._refuse(st, seg_id, "lease sealed/revoked; device cannot open again")
            return False
        if self._expired(st):
            self._refuse(st, seg_id, "lease expired; capability is time-bounded")
            return False
        if st.lease.scope != LeaseScope.FULL:
            self._refuse(st, seg_id, "scope cannot open normal segments")
            return False

        # Free in-window resume of the SAME paused segment.
        if st.paused and st.open_seg == seg_id and st.paused_at is not None:
            if self.clock.now() - st.paused_at <= RESUME_WINDOW:
                st.paused = False
                st.paused_at = None
                st.log.append(PlayEvent(self.clock.now(), EventKind.RESUME, seg_id, "in-window free"))
                self._emit(f"resume {seg_id}: within {RESUME_WINDOW}s, no extra credit")
                self._deliver(st, seg_id, seg.text)
                return True

        if st.credits_remaining <= 0:
            self._refuse(st, seg_id, "no listen credits left")
            return False

        # Content must be available: cached, or fetch now if online.
        if seg_id not in st.cache:
            if self.online:
                self.receive(lease_id, seg_id)
            if seg_id not in st.cache:
                self._refuse(st, seg_id, "segment never issued and player offline")
                st.log.append(PlayEvent(self.clock.now(), EventKind.MISSING, seg_id, "never issued"))
                return False

        # Spend one credit (non-rollback).
        st.plays_consumed += 1
        st.open_seg = seg_id
        st.opened_at = self.clock.now()
        st.paused = False
        st.paused_at = None
        st.log.append(
            PlayEvent(self.clock.now(), EventKind.OPEN, seg_id,
                      f"consumed {st.plays_consumed}/{st.lease.plays_total}")
        )
        self._emit(
            f"open {seg_id}: consumed credit {st.plays_consumed}/"
            f"{st.lease.plays_total} (remaining {st.credits_remaining})"
        )
        self._deliver(st, seg_id, seg.text)
        return True

    def pause(self, lease_id: str) -> bool:
        st = self._state(lease_id)
        if st is None or st.open_seg is None or st.paused:
            return False
        st.paused = True
        st.paused_at = self.clock.now()
        st.log.append(PlayEvent(self.clock.now(), EventKind.PAUSE, st.open_seg, "paused mid-segment"))
        self._emit(f"pause at {st.open_seg} (mid-segment)")
        return True

    def resume(self, lease_id: str) -> bool:
        st = self._state(lease_id)
        if st is None or st.open_seg is None or not st.paused:
            return False
        seg_id = st.open_seg
        if self._expired(st):
            self._refuse(st, seg_id, "lease expired; cannot resume")
            return False
        if self.clock.now() - (st.paused_at or 0) > RESUME_WINDOW:
            self._refuse(st, seg_id, "resume window passed; reopening costs a new credit")
            return False
        st.paused = False
        st.paused_at = None
        st.log.append(PlayEvent(self.clock.now(), EventKind.RESUME, seg_id,
                                "resumed mid-segment, no extra credit"))
        self._emit(f"resume {seg_id}: continuing mid-segment, no extra credit")
        seg = self._seg(st, seg_id)
        if seg is not None and seg_id in st.cache:
            self._deliver(st, seg_id, seg.text)
        return True

    def play_live(self, lease_id: str, seg_id: str) -> bool:
        st = self._state(lease_id)
        if st is None:
            raise ValueError("unknown lease")
        if not self.online:
            self._refuse(st, seg_id, "offline: preview-only cannot play")
            return False
        if st.sealed or st.state != LeaseState.ACTIVE:
            self._refuse(st, seg_id, "lease sealed/revoked")
            return False
        if self._expired(st):
            self._refuse(st, seg_id, "lease expired")
            return False
        seg = self._seg(st, seg_id)
        if seg is None or seg.kind != SegKind.PREVIEW_ONLY:
            self._refuse(st, seg_id, "not a preview-only segment")
            return False
        st.log.append(PlayEvent(self.clock.now(), EventKind.PLAY_LIVE, seg_id, "live, uncached"))
        self._emit(f"live {seg_id} (preview-only, live only, NOT cached)")
        self._emit(f"LIVE {seg_id}: {seg.text}")
        return True

    def _refuse(self, st: DeviceLeaseState, seg_id: str, why: str) -> None:
        self._emit(f"refused {seg_id}: {why}")
        st.log.append(PlayEvent(self.clock.now(), EventKind.REFUSED, seg_id, why))

    def _deliver(self, st: DeviceLeaseState, seg_id: str, text: str) -> None:
        self._emit(f"PLAY {seg_id}: {text}")

    # ---------------------------------------------------------------- honesty
    def missing_segments(self, lease_id: str) -> List[str]:
        """NORMAL segments never issued to this device (post-disconnect gaps)."""
        st = self._state(lease_id)
        if st is None:
            return []
        ch = get_chapter(st.lease.chapter_id)
        missing = []
        for s in ch.segments:
            if s.kind == SegKind.NORMAL and s.seg_id not in st.cache:
                missing.append(s.seg_id)
        return missing

    def narrate_missing(self, lease_id: str) -> List[str]:
        st = self._state(lease_id)
        if st is None:
            return []
        missing = self.missing_segments(lease_id)
        for seg_id in missing:
            st.log.append(PlayEvent(self.clock.now(), EventKind.MISSING, seg_id, "never issued; shown missing"))
            self._emit(f"MISSING {seg_id}: never issued to this device -- honest gap")
        return missing

    # ---------------------------------------------------------------- sealing
    def seal(self, lease_id: str) -> bool:
        st = self._state(lease_id)
        if st is None:
            return False
        if st.sealed:
            return True
        st.sealed = True
        st.state = LeaseState.REVOKED
        st.open_seg = None
        st.paused = False
        st.paused_at = None
        st.log.append(PlayEvent(self.clock.now(), EventKind.SEAL, None, "sealed; cannot open again"))
        self._emit(
            f"SEALED lease {lease_id}: cannot open again; counter frozen at "
            f"{st.plays_consumed}"
        )
        return True

    def return_proof(self, lease_id: str) -> ReturnProof:
        st = self._state(lease_id)
        if st is None:
            raise ValueError("unknown lease")
        if not st.sealed:
            raise ValueError("must seal before producing a return proof")
        log = [
            {"at": e.at, "kind": e.kind.value, "seg": e.seg_id, "detail": e.detail}
            for e in st.log
        ]
        proof = ReturnProof(
            lease_id=lease_id,
            chapter_id=st.lease.chapter_id,
            device_id=self.device_id,
            plays_consumed=st.plays_consumed,
            sealed=st.sealed,
            at=self.clock.now(),
            play_log=log,
        )
        proof = ReturnProof(
            **{**proof.__dict__,
               "signature": mac(device_secret(self.device_id), proof.payload())}
        )
        self._emit(f"emitted return proof: consumed={st.plays_consumed} sealed={st.sealed}")
        return proof

    def cached_segments(self, lease_id: str) -> List[str]:
        st = self._state(lease_id)
        return sorted(st.cache.keys()) if st else []

    def sealed_report(self, lease_id: str) -> Dict:
        """What an OFFLINE device can show on its own screen at the desk.

        This is NOT a signed network proof; it is just the device displaying its
        frozen counter. The desk may act on it only as a LIMITED provisional
        return, and only because the device is sealed (cannot open again).
        """
        st = self._state(lease_id)
        if st is None:
            raise ValueError("unknown lease")
        return {
            "lease_id": lease_id,
            "chapter_id": st.lease.chapter_id,
            "device_id": self.device_id,
            "sealed": st.sealed,
            "plays_consumed": st.plays_consumed,
            "plays_total": st.lease.plays_total,
        }
