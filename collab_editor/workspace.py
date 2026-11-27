"""File-backed local collaborative workspace.

A workspace is an ordinary directory.  Museum workspaces additionally own a
``private/`` directory; coop workspaces only receive allowlisted public data.
The public history is autonomous after delivery, so coop undo/redo works with
no museum process or network connection.
"""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .document import (
    clone_document,
    new_document,
    render_public,
    resolve_span,
    splice,
)
from .merge import merge_documents
from .privacy import (
    ANSWER_SCHEMA,
    packet_view,
    public_op_view,
    safe_json_dumps,
)

WORKSPACE_SCHEMA = "collab-workspace/v1"
PRIVATE_SCHEMA = "collab-museum-private/v1"
CONFLICT_SCHEMA = "collab-conflict/v1"
POLICY_SCHEMA = "collab-undo-policy/v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: Any, mode: int = 0o644) -> None:
    atomic_write_text(path, safe_json_dumps(value), mode)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def text_hash(doc: Dict[str, Any]) -> str:
    import hashlib

    payload = json.dumps(doc.get("anchors"), ensure_ascii=False, sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()[:16]


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.config_path = self.root / "collab.json"
        if not self.config_path.exists():
            raise FileNotFoundError(f"not a collab workspace: {self.root}")
        self.config = read_json(self.config_path)
        self.doc_path = self.root / "doc.json"
        self.history_path = self.root / "history.json"
        self.stacks_path = self.root / "stacks.json"
        self.sync_path = self.root / "sync.json"
        self.public_txt = self.root / "public.txt"
        self.doc = read_json(self.doc_path)
        self.history = read_json(self.history_path)
        self.stacks = read_json(self.stacks_path)
        self.sync = read_json(self.sync_path)
        self.private_path = self.root / "private" / "private.json"
        self.private: Optional[Dict[str, Any]] = None
        if self.private_path.exists():
            self.private = read_json(self.private_path)

    # ---------- setup and persistence ----------

    @classmethod
    def init(
        cls,
        root: str | Path,
        *,
        role: str,
        author: str,
        anchors: Sequence[Tuple[str, str]],
        force: bool = False,
    ) -> "Workspace":
        root = Path(root)
        if (root / "collab.json").exists() and not force:
            raise FileExistsError(f"workspace already exists: {root}")
        root.mkdir(parents=True, exist_ok=True)
        for dirname in ("outbox", "inbox", "reports", "conflicts"):
            (root / dirname).mkdir(exist_ok=True)

        config = {
            "schema": WORKSPACE_SCHEMA,
            "role": role,
            "author": author,
            "undo_policy": "autonomous-public-history/v1",
            "delivered_change_authority": "coop-branch-after-delivery",
            "public_seq": 0,
            "packet_seq": 0,
            "conflict_seq": 0,
            "created_at": utc_now(),
        }
        doc = new_document(anchors)
        history = {"schema": "collab-public-history/v1", "ops": [], "merged_packets": []}
        stacks = {"undo": [], "redo": []}
        base = clone_document(doc)
        sync = {
            "schema": "collab-sync/v1",
            "peers": {},
            "known_bases": {text_hash(base): base},
        }
        write_json(root / "collab.json", config)
        write_json(root / "doc.json", doc)
        write_json(root / "history.json", history)
        write_json(root / "stacks.json", stacks)
        write_json(root / "sync.json", sync)
        atomic_write_text(root / "public.txt", render_public(doc) + "\n")

        if role == "museum":
            private_dir = root / "private"
            private_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(private_dir, 0o700)
            private = {
                "schema": PRIVATE_SCHEMA,
                "next_internal_seq": 1,
                "annotations": [],
                "journal": [],
                "delivered_public_ops": {},
                "sent_packets": {},
                "confirmed_packets": [],
            }
            write_json(private_dir / "private.json", private, mode=0o600)
            os.chmod(private_dir / "private.json", 0o600)

        return cls(root)

    def save_config(self) -> None:
        write_json(self.config_path, self.config)

    def save_doc(self) -> None:
        write_json(self.doc_path, self.doc)
        atomic_write_text(self.public_txt, render_public(self.doc) + "\n")

    def save_history(self) -> None:
        write_json(self.history_path, self.history)

    def save_stacks(self) -> None:
        write_json(self.stacks_path, self.stacks)

    def save_sync(self) -> None:
        write_json(self.sync_path, self.sync)

    def save_private(self) -> None:
        if self.private is None:
            raise PermissionError("workspace has no museum-private store")
        write_json(self.private_path, self.private, mode=0o600)
        os.chmod(self.private_path, 0o600)

    def reload(self) -> "Workspace":
        return Workspace(self.root)

    @property
    def author(self) -> str:
        return self.config["author"]

    @property
    def role(self) -> str:
        return self.config["role"]

    def next_public_id(self) -> str:
        self.config["public_seq"] += 1
        initial = "".join(ch for ch in self.author if ch.isalnum())[:1].upper() or "X"
        op_id = f"PUB-{initial}-{self.config['public_seq']:04d}"
        self.save_config()
        return op_id

    def next_packet_id(self) -> str:
        self.config["packet_seq"] += 1
        packet_id = f"PKT-{self.author.upper()}-{self.config['packet_seq']:04d}"
        self.save_config()
        return packet_id

    def next_conflict_id(self) -> str:
        self.config["conflict_seq"] += 1
        conflict_id = f"CF-{self.config['conflict_seq']:04d}"
        self.save_config()
        return conflict_id

    def next_internal_id(self, prefix: str) -> str:
        if self.private is None:
            raise PermissionError("internal ids exist only in museum-private store")
        seq = self.private["next_internal_seq"]
        self.private["next_internal_seq"] += 1
        return f"{prefix}-{seq:04d}"

    def public_text(self) -> str:
        return self.public_txt.read_text(encoding="utf-8")

    # ---------- public editing, undo and redo ----------

    def _append_public_op(self, op: Dict[str, Any]) -> None:
        self.history["ops"].append(op)
        self.doc["head"] = op["id"]
        self.doc["rev"] = int(self.doc.get("rev", 0)) + 1

    def edit(
        self,
        anchor_id: str,
        new_text: str,
        *,
        start: Optional[int] = None,
        end: Optional[int] = None,
        old: Optional[str] = None,
        occurrence: int = 0,
        kind: str = "edit",
        extra: Optional[Dict[str, Any]] = None,
        push_undo: bool = True,
    ) -> Dict[str, Any]:
        old_head = self.doc["head"]
        anchor = next(a for a in self.doc["anchors"] if a["id"] == anchor_id)
        s, e, removed = resolve_span(
            anchor["text"], start=start, end=end, old=old, occurrence=occurrence
        )
        change = splice(self.doc, anchor_id, s, e, new_text, expected_removed=removed)

        op: Dict[str, Any] = {
            "id": self.next_public_id(),
            "kind": kind,
            "author": self.author,
            "created_at": utc_now(),
            "base_head": old_head,
            "anchor": anchor_id,
            "start": s,
            "end": e,
            "removed": removed,
            "inserted": new_text,
        }
        if extra:
            # Callers must not add private fields here.
            op.update(extra)
        self._append_public_op(op)

        if push_undo:
            inverse_end = s + len(new_text)
            self.stacks["undo"].append(
                {
                    "op_id": op["id"],
                    "forward": {
                        "start": s,
                        "end": e,
                        "inserted": new_text,
                        "expected_removed": removed,
                    },
                    "inverse": {
                        "start": s,
                        "end": inverse_end,
                        "inserted": removed,
                        "expected_current": new_text,
                    },
                }
            )
            self.stacks["redo"] = []
            self.save_stacks()

        self.save_doc()
        self.save_history()
        return op

    def _find_current_span(self, anchor_id: str, planned: Dict[str, Any]) -> Tuple[int, int, str]:
        text = next(a for a in self.doc["anchors"] if a["id"] == anchor_id)["text"]
        s, e = int(planned["start"]), int(planned["end"])
        expected = planned.get("expected_current")
        if 0 <= s <= e <= len(text) and (expected is None or text[s:e] == expected):
            return s, e, text[s:e]

        # After a merge or another local edit, coordinates may have moved.
        # A unique expected string lets us recover without exposing anything
        # private.  If it is not unique, refuse rather than guessing a sentence.
        if expected:
            matches: List[int] = []
            at = text.find(expected)
            while at != -1:
                matches.append(at)
                at = text.find(expected, at + max(1, len(expected)))
            if len(matches) == 1:
                start = matches[0]
                return start, start + len(expected), expected
        raise ValueError(f"undo/redo span is no longer unambiguous at {anchor_id}")

    def undo(self, op_id: Optional[str] = None) -> Dict[str, Any]:
        if op_id is not None:
            matches = [i for i, e in enumerate(self.stacks["undo"]) if e["op_id"] == op_id]
            if not matches:
                raise PermissionError(f"undoable own operation not found: {op_id}")
            index = matches[-1]
            entry = self.stacks["undo"][index]
        else:
            if not self.stacks["undo"]:
                raise RuntimeError("nothing to undo")
            entry = self.stacks["undo"][-1]
            index = len(self.stacks["undo"]) - 1

        original = next((o for o in self.history["ops"] if o["id"] == entry["op_id"]), None)
        if original is not None and original.get("author") != self.author:
            raise PermissionError("an author may only undo their own operation")
        if self.role == "museum" and self.is_public_op_delivered(entry["op_id"]):
            raise PermissionError(
                "This public change has entered the coop autonomous history. "
                "Append a new public correction instead of directly undoing it."
            )

        inv = entry["inverse"]
        s, e, current = self._find_current_span(entry["op_id"] and original["anchor"], inv)
        old_head = self.doc["head"]
        removed = current
        inserted = inv["inserted"]
        splice(self.doc, original["anchor"], s, e, inserted, expected_removed=removed)

        undo_op: Dict[str, Any] = {
            "id": self.next_public_id(),
            "kind": "undo",
            "author": self.author,
            "created_at": utc_now(),
            "base_head": old_head,
            "undo_of": entry["op_id"],
            "anchor": original["anchor"],
            "start": s,
            "end": e,
            "removed": removed,
            "inserted": inserted,
        }
        self._append_public_op(undo_op)

        self.stacks["undo"].pop(index)
        self.stacks["redo"].append(
            {
                "op_id": entry["op_id"],
                "undo_op_id": undo_op["id"],
                "forward": {
                    "start": s,
                    "end": s + len(inserted),
                    "inserted": entry["forward"]["inserted"],
                    "expected_removed": inserted,
                },
            }
        )
        self.save_doc()
        self.save_history()
        self.save_stacks()
        return undo_op

    def redo(self) -> Dict[str, Any]:
        if not self.stacks["redo"]:
            raise RuntimeError("nothing to redo")
        entry = self.stacks["redo"][-1]
        anchor_id = entry["forward"].get("anchor")
        if anchor_id is None:
            original = next(o for o in self.history["ops"] if o["id"] == entry["op_id"])
            anchor_id = original["anchor"]
        fwd = entry["forward"]
        s, e, removed = self._find_current_span(anchor_id, fwd)
        old_head = self.doc["head"]
        inserted = fwd["inserted"]
        splice(self.doc, anchor_id, s, e, inserted, expected_removed=removed)

        redo_op: Dict[str, Any] = {
            "id": self.next_public_id(),
            "kind": "redo",
            "author": self.author,
            "created_at": utc_now(),
            "base_head": old_head,
            "redo_of": entry["op_id"],
            "undo_of": entry.get("undo_op_id"),
            "anchor": anchor_id,
            "start": s,
            "end": e,
            "removed": removed,
            "inserted": inserted,
        }
        self._append_public_op(redo_op)

        self.stacks["redo"].pop()
        self.stacks["undo"].append(
            {
                "op_id": entry["op_id"],
                "forward": entry.get("original_forward", fwd),
                "inverse": {
                    "start": s,
                    "end": s + len(inserted),
                    "inserted": removed,
                    "expected_current": inserted,
                },
            }
        )
        self.save_doc()
        self.save_history()
        self.save_stacks()
        return redo_op

    def withdraw_delivered(self, public_op_id: str) -> Dict[str, Any]:
        """Exercise coop branch authority over a delivered public change.

        This is not author-history erasure: it appends a new coop-authored
        withdrawal that reverses the public effect in the autonomous history.
        """
        if self.role != "coop":
            raise PermissionError(
                "After delivery, withdrawal authority for the public branch belongs to coop"
            )
        target = self._public_op_by_id(public_op_id)
        if not target.get("anchor") or "inserted" not in target or "removed" not in target:
            raise ValueError(f"public operation {public_op_id} has no reversible splice")

        anchor_id = target["anchor"]
        anchor = next(a for a in self.doc["anchors"] if a["id"] == anchor_id)
        current = target.get("inserted", "")
        replacement = target.get("removed", "")
        matches: List[int] = []
        if current:
            at = anchor["text"].find(current)
            while at != -1:
                matches.append(at)
                at = anchor["text"].find(current, at + max(1, len(current)))
        if len(matches) == 1:
            start = matches[0]
            end = start + len(current)
        else:
            start = int(target.get("start", 0))
            end = start + len(current)
        return self.edit(
            anchor_id,
            replacement,
            start=start,
            end=end,
            kind="withdrawal",
            extra={"withdraws": public_op_id},
            push_undo=True,
        )

    # ---------- museum-private notes and mixed transactions ----------

    def _recompute_private_positions(self) -> None:
        if self.private is None:
            return
        parts: List[str] = []
        active = [a for a in self.private["annotations"] if not a.get("withdrawn")]
        for anchor in self.doc["anchors"]:
            before = [
                a
                for a in active
                if a["anchor"] == anchor["id"] and a.get("placement", "before") == "before"
            ]
            after = [
                a
                for a in active
                if a["anchor"] == anchor["id"] and a.get("placement") == "after"
            ]
            for note in before:
                start = sum(len(p) for p in parts)
                marker = f"〖私注 {note['id']}〗"
                parts.append(marker)
                parts.append(note["text"])
                parts.append("\n")
                end = sum(len(p) for p in parts)
                note["full_text_start"] = start
                note["full_text_end"] = end
            parts.append(anchor["text"])
            if not anchor["text"].endswith("\n"):
                parts.append("\n")
            for note in after:
                start = sum(len(p) for p in parts)
                marker = f"〖私注 {note['id']}〗"
                parts.append(marker)
                parts.append(note["text"])
                parts.append("\n")
                end = sum(len(p) for p in parts)
                note["full_text_start"] = start
                note["full_text_end"] = end

    def _reverse_private_note_for_undo(self, entry: Dict[str, Any]) -> None:
        if self.private is None:
            return
        note = next(
            (n for n in self.private["annotations"] if n["id"] == entry["annotation_id"]),
            None,
        )
        if note is None:
            return
        if entry.get("note_action") == "updated" and entry.get("note_before"):
            before = deepcopy(entry["note_before"])
            note.clear()
            note.update(before)
            note["restored_by_mixed_undo"] = entry["id"]
        else:
            note["withdrawn"] = True
            note["withdrawal"] = "mixed-operation-undo"

    def add_private_note(
        self, anchor_id: str, text: str, placement: str = "before"
    ) -> Dict[str, Any]:
        if self.private is None:
            raise PermissionError("coop/public workspaces cannot store private notes")
        if not any(a["id"] == anchor_id for a in self.doc["anchors"]):
            raise KeyError(anchor_id)
        internal_id = self.next_internal_id("PV")
        note_id = "NT-" + internal_id.split("-", 1)[1]
        note = {
            "id": note_id,
            "internal_op_id": internal_id,
            "anchor": anchor_id,
            "placement": placement,
            "text": text,
            "created_at": utc_now(),
            "full_text_start": None,
            "full_text_end": None,
        }
        self.private["annotations"].append(note)
        self.private["journal"].append(
            {
                "id": internal_id,
                "kind": "pure-private-note",
                "created_at": utc_now(),
                "annotation_id": note_id,
                "anchor": anchor_id,
            }
        )
        self._recompute_private_positions()
        self.save_private()
        return note

    def mixed_edit(
        self,
        anchor_id: str,
        new_text: str,
        note_text: str,
        *,
        start: Optional[int] = None,
        end: Optional[int] = None,
        old: Optional[str] = None,
        occurrence: int = 0,
        placement: str = "before",
        note_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.private is None:
            raise PermissionError("mixed museum edits require private storage")
        internal_id = self.next_internal_id("MX")
        op = self.edit(
            anchor_id,
            new_text,
            start=start,
            end=end,
            old=old,
            occurrence=occurrence,
            kind="edit",
            push_undo=False,
        )

        note_action = "created"
        note_before: Optional[Dict[str, Any]] = None
        if note_id is not None:
            note = next(
                (n for n in self.private["annotations"] if n["id"] == note_id),
                None,
            )
            if note is None:
                raise KeyError(f"private annotation not found: {note_id}")
            note_action = "updated"
            note_before = deepcopy(note)
            note["text"] = note_text
            note["mixed_op_id"] = internal_id
            note["updated_at"] = utc_now()
        else:
            note_id = "NT-" + internal_id.split("-", 1)[1]
            note = {
                "id": note_id,
                "mixed_op_id": internal_id,
                "anchor": anchor_id,
                "placement": placement,
                "text": note_text,
                "created_at": utc_now(),
                "full_text_start": None,
                "full_text_end": None,
            }
            self.private["annotations"].append(note)

        self.private["journal"].append(
            {
                "id": internal_id,
                "kind": "mixed-edit",
                "created_at": utc_now(),
                "public_op_id": op["id"],
                "annotation_id": note["id"],
                "note_action": note_action,
                "note_before": note_before,
                "status": "active",
            }
        )
        self._recompute_private_positions()
        self.save_private()
        return {"internal_id": internal_id, "public_op": op, "annotation": note}

    def _public_op_by_id(self, op_id: str) -> Dict[str, Any]:
        return next(o for o in self.history["ops"] if o["id"] == op_id)

    def is_public_op_delivered(self, public_op_id: str) -> bool:
        if self.private is None:
            return False
        record = self.private["delivered_public_ops"].get(public_op_id)
        return bool(record and record.get("confirmed", record.get("sent")))

    def undo_mixed(self, internal_id: str) -> Dict[str, Any]:
        if self.private is None:
            raise PermissionError("only museum can adjudicate its mixed operation")
        entry = next((e for e in self.private["journal"] if e["id"] == internal_id), None)
        if entry is None or entry.get("kind") != "mixed-edit":
            raise KeyError(f"mixed operation not found: {internal_id}")
        if entry.get("status") in {"whole-undone", "correction-proposed"}:
            raise RuntimeError(f"{internal_id} already withdrawn")

        pub = self._public_op_by_id(entry["public_op_id"])
        note = next(
            (n for n in self.private["annotations"] if n["id"] == entry["annotation_id"]),
            None,
        )
        delivered = self.is_public_op_delivered(pub["id"])

        if not delivered:
            # True whole-transaction rollback is allowed only before delivery.
            s = int(pub["start"])
            e = s + len(pub.get("inserted", ""))
            current_span = self.doc["anchors"]
            anchor = next(a for a in self.doc["anchors"] if a["id"] == pub["anchor"])
            if not (0 <= s <= e <= len(anchor["text"])) or anchor["text"][s:e] != pub["inserted"]:
                # Locate uniquely if a local later edit moved coordinates.
                s, e, _ = self._find_current_span(
                    pub["anchor"],
                    {"start": s, "end": e, "expected_current": pub.get("inserted", "")},
                )
            undo_op = self.edit(
                pub["anchor"],
                pub["removed"],
                start=s,
                end=e,
                kind="undo",
                extra={"undo_of": pub["id"]},
                push_undo=False,
            )
            if note is not None:
                self._reverse_private_note_for_undo(entry)
            entry["status"] = "whole-undone"
            entry["undo_public_op_id"] = undo_op["id"]
            self._recompute_private_positions()
            self.save_private()
            return {
                "policy": "whole-mixed-undo",
                "internal_id": internal_id,
                "public_undo": undo_op,
            }

        # Delivered public history is autonomous at the coop branch.  Remove
        # the private half locally and propose a brand-new public correction.
        target = pub.get("removed", "")
        anchor = next(a for a in self.doc["anchors"] if a["id"] == pub["anchor"])
        matches: List[int] = []
        if target:
            at = anchor["text"].find(target)
            while at != -1:
                matches.append(at)
                at = anchor["text"].find(target, at + max(1, len(target)))
        if len(matches) == 1:
            s = matches[0]
            e = s + len(target)
        else:
            # Fall back to reversing the known post-edit span.
            s = int(pub["start"])
            e = s + len(pub.get("inserted", ""))
        correction = self.edit(
            pub["anchor"],
            target,
            start=s,
            end=e,
            old=None if len(matches) == 1 else pub.get("inserted"),
            kind="correction",
            extra={"corrects": pub["id"]},
            push_undo=False,
        )
        if note is not None:
            self._reverse_private_note_for_undo(entry)
            note["private_cancellation"] = "public half requires a separate correction"
        entry["status"] = "correction-proposed"
        entry["correction_public_op_id"] = correction["id"]
        self._recompute_private_positions()
        self.save_private()
        return {
            "policy": "public-correction-proposed",
            "internal_id": internal_id,
            "public_correction": correction,
            "notice": (
                "Delivered public change is owned by autonomous coop history; "
                "the mixed transaction cannot be erased wholesale."
            ),
        }

    # ---------- delivery and packets ----------

    def _peer_base(self, peer: str) -> Dict[str, Any]:
        peer_state = self.sync["peers"].setdefault(peer, {})
        if "confirmed_base_doc" not in peer_state:
            base = clone_document(self.doc)
            peer_state["confirmed_base_doc"] = base
            peer_state["confirmed_base_hash"] = text_hash(base)
            peer_state["confirmed_head"] = self.doc["head"]
            self.save_sync()
        return peer_state["confirmed_base_doc"]

    def deliver_to(self, target_root: str | Path, *, exchange_packet: Optional[str | Path] = None) -> Dict[str, Any]:
        if self.role != "museum":
            raise PermissionError("initial autonomous public history is delivered by museum")
        target_root = Path(target_root)
        anchors = [(a["id"], a["text"]) for a in self.doc["anchors"]]
        coop = Workspace.init(
            target_root,
            role="coop",
            author="coop",
            anchors=anchors,
            force=True,
        )
        coop.doc = clone_document(self.doc)
        coop.history = {
            "schema": "collab-public-history/v1",
            "ops": [public_op_view(op) for op in self.history["ops"]],
            "merged_packets": [],
        }
        coop.stacks = {"undo": [], "redo": []}
        base = clone_document(self.doc)
        coop.sync["peers"]["museum"] = {
            "confirmed_base_doc": clone_document(base),
            "confirmed_base_hash": text_hash(base),
            "confirmed_head": self.doc["head"],
        }
        coop.sync["known_bases"][text_hash(base)] = clone_document(base)
        policy = {
            "schema": POLICY_SCHEMA,
            "delivered_change_undo_authority": "coop-branch",
            "own_operation_undo": "offline-allowed",
            "museum_post_delivery_undo": "new-public-correction-only",
            "alignment_base": "last_confirmed_public_document",
        }
        write_json(target_root / "undo-policy.json", policy)
        coop.save_doc()
        coop.save_history()
        coop.save_stacks()
        coop.save_sync()
        write_json(target_root / "undo-policy.json", policy)

        self.sync["peers"]["coop"] = {
            "confirmed_base_doc": clone_document(base),
            "confirmed_base_hash": text_hash(base),
            "confirmed_head": self.doc["head"],
        }
        self.save_sync()

        packet = None
        if exchange_packet is not None:
            packet = self.push(exchange_packet, peer="coop", kind="delivery")

        if self.private is not None:
            for op in self.history["ops"]:
                self.private["delivered_public_ops"][op["id"]] = {
                    "delivered": True,
                    "confirmed": True,
                    "at": utc_now(),
                    "packet_id": packet["packet_id"] if packet else None,
                }
            if packet is not None:
                self.private["confirmed_packets"].append(packet["packet_id"])
            self.save_private()

        return {"coop": coop, "packet": packet, "policy": policy}

    def push(
        self,
        out_path: str | Path,
        *,
        peer: Optional[str] = None,
        kind: str = "push",
    ) -> Dict[str, Any]:
        peer = peer or ("coop" if self.role == "museum" else "museum")
        base_doc = self._peer_base(peer)
        packet_id = self.next_packet_id()
        packet = packet_view(
            packet_id=packet_id,
            author=self.author,
            created_at=utc_now(),
            base_ref=self.sync["peers"][peer].get("confirmed_head", "common-public-base"),
            base_doc=clone_document(base_doc),
            doc=clone_document(self.doc),
            head=self.doc["head"],
            changes=list(self.history["ops"]),
            kind=kind,
        )
        out_path = Path(out_path)
        write_json(out_path, packet)
        write_json(self.root / "outbox" / f"{packet_id}.json", packet)

        self.sync["peers"].setdefault(peer, {})["last_sent_packet"] = packet_id
        self.save_sync()

        if self.private is not None:
            self.private["sent_packets"][packet_id] = {
                "path": str(out_path),
                "at": utc_now(),
                "kind": kind,
            }
            # Public ops in this packet are sent, but not confirmed until ack.
            for op in packet["changes"]:
                record = self.private["delivered_public_ops"].setdefault(op["id"], {})
                record["sent"] = True
                record["sent_packet"] = packet_id
            self.save_private()
        return packet

    def _remember_remote_ops(self, packet: Dict[str, Any]) -> List[str]:
        """Store allowlisted remote public changes in the local public history.

        Packets may echo changes already known through an earlier merge, so ids
        are de-duplicated.  Remote operations are deliberately not placed on
        the local personal undo stack.
        """
        known = {op.get("id") for op in self.history["ops"]}
        added: List[str] = []
        for raw in packet.get("changes", []):
            op = public_op_view(raw)
            op_id = op.get("id")
            if not op_id or op_id in known:
                continue
            self.history["ops"].append(op)
            known.add(op_id)
            added.append(op_id)
        return added

    def _write_conflict_sheet(
        self, packet: Dict[str, Any], merged: Dict[str, Any]
    ) -> Tuple[str, Path, Path]:
        conflict_id = self.next_conflict_id()
        payload = {
            "schema": CONFLICT_SCHEMA,
            "id": conflict_id,
            "packet_id": packet["packet_id"],
            "created_at": utc_now(),
            "status": "conflict",
            "affected_locations": merged["affected_locations"],
            "items": merged["conflicts"],
            "local_doc": self.doc,
            "remote_doc": packet["doc"],
        }
        json_path = self.root / "conflicts" / f"{conflict_id}.json"
        txt_path = self.root / "conflicts" / f"{conflict_id}.txt"
        write_json(json_path, payload)

        lines = [
            f"Public merge conflict {conflict_id}",
            f"From packet: {packet['packet_id']}",
            "",
        ]
        for item in merged["conflicts"]:
            lines.extend(
                [
                    f"Anchor: {item.get('anchor')}",
                    f"Public base characters: {item.get('base_start')}..{item.get('base_end')}",
                    f"Problem: {item.get('message')}",
                    f"Base excerpt: {item.get('base_excerpt')!r}",
                    "",
                ]
            )
        atomic_write_text(txt_path, "\n".join(lines) + "\n")
        write_json(self.root / "conflicts" / "latest.json", payload)
        return conflict_id, json_path, txt_path

    def merge_packet(
        self,
        packet_path: str | Path,
        *,
        answer_out: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        packet = read_json(Path(packet_path))
        if packet.get("schema") != "collab-public-packet/v1":
            raise ValueError("not a public packet")
        if packet["author"] == self.author:
            return {"status": "ignored-self-packet", "packet_id": packet["packet_id"]}
        if packet["packet_id"] in self.history.get("merged_packets", []):
            return {
                "status": "up-to-date",
                "packet_id": packet["packet_id"],
                "head": self.doc["head"],
            }

        peer = packet["author"]
        peer_state = self.sync["peers"].setdefault(peer, {})
        known_bases = self.sync.setdefault("known_bases", {})
        base_hash = text_hash(packet["base_doc"])

        # A packet may be based on this workspace's current draft (a normal
        # incremental push) or on any explicitly stored common base.  We must
        # validate before adopting the untrusted packet base as "known".
        confirmed = peer_state.get("confirmed_base_doc")
        known_hashes = set(known_bases.keys())
        known_hashes.add(text_hash(self.doc))
        if confirmed is not None:
            known_hashes.add(text_hash(confirmed))
        if base_hash not in known_hashes:
            raise ValueError(
                f"packet {packet['packet_id']} is not based on a known common public draft"
            )
        known_bases.setdefault(base_hash, clone_document(packet["base_doc"]))

        old_head = self.doc["head"]
        merged = merge_documents(
            packet["base_doc"],
            self.doc,
            packet["doc"],
            local_name=self.author,
            remote_name=packet["author"],
        )

        report: Dict[str, Any] = {
            "packet_id": packet["packet_id"],
            "in_reply_to": packet["packet_id"],
            "workspace": self.author,
            "answer_author": self.author,
            "created_at": utc_now(),
            "base_hash": base_hash,
            "old_head": old_head,
            "affected_locations": merged["affected_locations"],
            "conflicts": merged["conflicts"],
        }

        if merged["status"] == "conflict":
            conflict_id, json_file, txt_file = self._write_conflict_sheet(packet, merged)
            report.update(
                {
                    "status": "conflict",
                    "conflict_id": conflict_id,
                    "conflict_json": str(json_file),
                    "conflict_text": str(txt_file),
                    "head": self.doc["head"],
                }
            )
            write_json(self.root / "reports" / f"{packet['packet_id']}-report.json", report)
            if answer_out is not None:
                answer = {
                    "schema": ANSWER_SCHEMA,
                    **report,
                    "doc": self.doc,
                }
                write_json(Path(answer_out), answer)
            return report

        self.doc = merged["doc"]
        imported_changes = self._remember_remote_ops(packet)
        merge_op = {
            "id": self.next_public_id(),
            "kind": "merge",
            "author": self.author,
            "created_at": utc_now(),
            "base_head": old_head,
            "merge_from": packet["packet_id"],
            "packet_id": packet["packet_id"],
            "parents": [old_head, packet.get("head")],
            "affected_locations": merged["affected_locations"],
        }
        self._append_public_op(merge_op)
        self.history.setdefault("merged_packets", []).append(packet["packet_id"])

        # If the merged document equals the sender's document, this end now
        # knows the sender's exact head and can use it as a confirmed base.
        from .document import documents_equal

        if documents_equal(self.doc, packet["doc"]):
            peer_state["confirmed_base_doc"] = clone_document(self.doc)
            peer_state["confirmed_base_hash"] = text_hash(self.doc)
            peer_state["confirmed_head"] = self.doc["head"]
        else:
            peer_state["last_remote_doc"] = clone_document(packet["doc"])
        peer_state["last_merged_packet"] = packet["packet_id"]
        known_bases[text_hash(self.doc)] = clone_document(self.doc)

        report.update(
            {
                "status": "merged",
                "new_head": self.doc["head"],
                "head": self.doc["head"],
                "merge_op_id": merge_op["id"],
                "imported_changes": imported_changes,
                "affected_count": len(merged["affected_locations"]),
                "conflict_count": 0,
            }
        )
        write_json(self.root / "reports" / f"{packet['packet_id']}-report.json", report)
        self.save_doc()
        self.save_history()
        self.save_sync()

        if answer_out is not None:
            answer = {
                "schema": ANSWER_SCHEMA,
                **report,
                "doc": clone_document(self.doc),
            }
            write_json(Path(answer_out), answer)
        return report

    def acknowledge_answer(self, answer_path: str | Path) -> Dict[str, Any]:
        answer = read_json(Path(answer_path))
        if answer.get("schema") != ANSWER_SCHEMA:
            raise ValueError("not a merge answer")
        author = answer.get("answer_author", answer.get("workspace"))
        peer_state = self.sync["peers"].setdefault(author, {})
        if answer.get("status") == "merged" and "doc" in answer:
            peer_state["confirmed_base_doc"] = clone_document(answer["doc"])
            peer_state["confirmed_base_hash"] = text_hash(answer["doc"])
            peer_state["confirmed_head"] = answer.get("head")
            self.sync.setdefault("known_bases", {})[text_hash(answer["doc"])] = clone_document(
                answer["doc"]
            )
        self.save_sync()

        if self.private is not None:
            packet_id = answer.get("in_reply_to")
            if packet_id:
                self.private["confirmed_packets"].append(packet_id)
                record = self.private["sent_packets"].setdefault(packet_id, {})
                record["acknowledged"] = True
                for pub_id, delivery in self.private["delivered_public_ops"].items():
                    if delivery.get("sent_packet") == packet_id:
                        delivery["confirmed"] = True
                self.save_private()
        return {"status": "acknowledged", "answer": answer.get("answer_id", answer.get("in_reply_to"))}

    def resolve_conflict(
        self, conflict_id: str, choice: str, *, text: Optional[str] = None
    ) -> Dict[str, Any]:
        path = self.root / "conflicts" / f"{conflict_id}.json"
        conflict = read_json(path)
        old_head = self.doc["head"]
        if choice == "ours":
            self.doc = clone_document(conflict["local_doc"])
        elif choice == "theirs":
            self.doc = clone_document(conflict["remote_doc"])
        elif choice == "text":
            if text is None:
                raise ValueError("choice=text requires text")
            # Resolve the first item's anchor with supplied full anchor text.
            item = conflict["items"][0]
            anchor = next(a for a in self.doc["anchors"] if a["id"] == item["anchor"])
            anchor["text"] = text
        else:
            raise ValueError("choice must be ours, theirs, or text")

        op = {
            "id": self.next_public_id(),
            "kind": "conflict-resolution",
            "author": self.author,
            "created_at": utc_now(),
            "base_head": old_head,
            "conflict_id": conflict_id,
            "choice": choice,
        }
        self._append_public_op(op)
        self.save_doc()
        self.save_history()
        return op
