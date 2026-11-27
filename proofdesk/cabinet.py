from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .common import ensure_dir, read_json, write_json_atomic
from .trust import LocalTrust


class TrustedCabinet:
    """Filesystem send/receive cabinet for credentials and revision pieces.

    Each drawer is a principal/service.  ``connected=False`` leaves a sealed
    envelope in the sender's outbox; :meth:`pump` represents reconnecting the
    local route.  A credential already in somebody's drawer does not need the
    editor-station/library network link, which is exactly the portable-pass
    case.
    """

    def __init__(self, root: Path, trust: LocalTrust):
        self.root = ensure_dir(Path(root))
        self.trust = trust
        self.seq_path = self.root / "cabinet-seq.json"
        state = read_json(self.seq_path, default={"seq": 0}) or {"seq": 0}
        self.seq = int(state.get("seq", 0))

    def _next_seq(self) -> int:
        self.seq += 1
        write_json_atomic(self.seq_path, {"seq": self.seq})
        return self.seq

    def _dir(self, owner: str, box: str) -> Path:
        return ensure_dir(self.root / owner / box)

    def send(
        self,
        sender: str,
        recipient: str,
        kind: str,
        body: dict[str, Any],
        *,
        connected: bool = True,
    ) -> dict[str, Any]:
        seq = self._next_seq()
        envelope: dict[str, Any] = {
            "id": f"env-{seq:06d}",
            "seq": seq,
            "kind": kind,
            "sender": sender,
            "recipient": recipient,
            "body": body,
        }
        envelope = self.trust.sign(sender, envelope)
        stem = f"{seq:06d}-{envelope['id']}"

        sent_path = self._dir(sender, "sent") / f"{stem}.json"
        write_json_atomic(sent_path, envelope)

        if connected:
            inbox_path = self._dir(recipient, "inbox") / f"{stem}.json"
            write_json_atomic(inbox_path, envelope)
            envelope["_delivered_path"] = str(inbox_path)
        else:
            outbox_path = (
                self._dir(sender, "outbox")
                / f"{stem}-to-{recipient}.json"
            )
            write_json_atomic(outbox_path, envelope)
            envelope["_outbox_path"] = str(outbox_path)
        return envelope

    def pump(self, sender: str, recipient: str) -> list[dict[str, Any]]:
        """Deliver queued outbox envelopes after a route is restored."""
        outbox = self._dir(sender, "outbox")
        delivered: list[dict[str, Any]] = []
        for path in sorted(outbox.glob("*.json")):
            envelope = read_json(path)
            if envelope.get("recipient") != recipient:
                continue
            if not self.trust.verify(envelope):
                raise ValueError(f"cabinet envelope failed verification: {path}")
            seq = int(envelope["seq"])
            target = (
                self._dir(recipient, "inbox")
                / f"{seq:06d}-{envelope['id']}.json"
            )
            write_json_atomic(target, envelope)
            path.unlink()
            delivered.append(envelope)
        return delivered

    def list_inbox(self, recipient: str, kind: str | None = None) -> list[dict[str, Any]]:
        inbox = self._dir(recipient, "inbox")
        envelopes: list[dict[str, Any]] = []
        for path in sorted(inbox.glob("*.json")):
            env = read_json(path)
            env["_path"] = str(path)
            if kind is None or env.get("kind") == kind:
                envelopes.append(env)
        return envelopes

    def take_inbox(self, recipient: str, kind: str | None = None) -> list[dict[str, Any]]:
        """Move inbox mail to taken/ and return it."""
        taken_dir = self._dir(recipient, "taken")
        result: list[dict[str, Any]] = []
        for env in self.list_inbox(recipient, kind):
            path = Path(env.pop("_path"))
            target = taken_dir / path.name
            shutil.move(str(path), str(target))
            env["_taken_path"] = str(target)
            result.append(env)
        return result

    def verify_envelope(self, envelope: dict[str, Any]) -> bool:
        return self.trust.verify(envelope)
