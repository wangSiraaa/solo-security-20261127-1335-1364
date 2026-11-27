"""Core input-method engine: merged model + sealed compensating receipts.

See DESIGN.md for the full argument.  Summary:

* The device keeps one shared count table for "which word follows which word"
  (plus sentence-start counts).  It is updated from a public base corpus and
  from members' allowed sentences.  The table holds only counts, never who
  taught them.

* For every member the device keeps a device-sealed *compensating receipt*: the
  exact multiset of sentences that member taught.  On withdrawal the receipt is
  used to subtract that member's contribution from the shared table, so the
  result is count-for-count identical to a tablet that never saw them.  Because
  the receipt lives on the device (sealed under the device key), revocation
  works even after the member walks away with their own token.

* Ordinary users cannot read the receipts (they are sealed), so they cannot
  learn who taught what.  Nobody can tell a withdrawn member ever existed from
  the counts: after subtraction the table equals the never-had baseline.

* Suggestion order is ``(count desc, word asc)`` on owner-free counts, so order
  never carries a per-member or recency signal.  No decrypted text is ever
  written to disk, so a reboot cannot resurrect a withdrawn contribution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import seal

VERSION = 2

_PAD_BUCKET = 128

# ``prev`` key for a sentence start.  Real synthetic words are never empty.
START = ""


class ImeError(Exception):
    pass


class AuthError(ImeError):
    pass


class ReplayError(ImeError):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pad(data: bytes) -> bytes:
    raw = len(data).to_bytes(4, "big") + data
    n = max(_PAD_BUCKET, ((len(raw) + _PAD_BUCKET - 1) // _PAD_BUCKET) * _PAD_BUCKET)
    return raw + bytes(n - len(raw))


def _unpad(data: bytes) -> bytes:
    if len(data) < 4:
        raise ImeError("padded payload too short")
    n = int.from_bytes(data[:4], "big")
    if n > len(data) - 4:
        raise ImeError("padded payload length out of range")
    return data[4:4 + n]


def normalize_sentence(sentence: str) -> str:
    words = sentence.strip().split()
    if not words:
        raise ValueError("empty sentence")
    return " ".join(words)


def sentence_transitions(sentence: str) -> Dict[Tuple[str, str], int]:
    """Multiset of (previous, next) pairs in a sentence, including the start."""
    words = sentence.split(" ")
    pairs: List[Tuple[str, str]] = [(START, words[0])]
    pairs.extend(zip(words, words[1:]))
    counts: Dict[Tuple[str, str], int] = {}
    for p, n in pairs:
        counts[(p, n)] = counts.get((p, n), 0) + 1
    return counts


class Store:
    """Persistence interface."""

    def load(self) -> Optional[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def save(self, data: dict) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class MemoryStore(Store):
    def __init__(self, data: Optional[dict] = None):
        self.data = data

    def load(self) -> Optional[dict]:
        return None if self.data is None else json.loads(json.dumps(self.data))

    def save(self, data: dict) -> None:
        self.data = json.loads(json.dumps(data))


class JsonStore(Store):
    """Atomic JSON-file persistence (crash-safe via temp file + replace)."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Optional[dict]:
        import os

        if not os.path.exists(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict) -> None:
        import os
        import tempfile

        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".synthime-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class Device:
    """A shareable local input method with revocable per-member learning.

    ``master_key`` (32 bytes) seals the device-side material.  If omitted the
    device generates one and stores it in the state file (convenience mode: the
    receipts stay opaque to ordinary users, but a file-level attacker holding
    the whole file could open them).  Pass an externally kept key for the
    stronger guarantee.
    """

    def __init__(self, store: Optional[Store] = None, master_key: Optional[bytes] = None):
        self.store = store or MemoryStore()
        self._master = master_key
        raw = self.store.load()
        if raw is None:
            self._kdev = master_key or seal.new_key()
            self._model: Dict[str, Dict[str, int]] = {}
            self._receipts: Dict[str, dict] = {}
            self.state = {
                "version": VERSION,
                "members": {},
                "revoked": {},
                "base": [],
                "local": {"current": None, "disabled": {}},
                "sealed_model": None,
                "receipts": {},
                "kdev": None if master_key else _b64(self._kdev),
            }
            self._base_cache = self._base_trans(self.state["base"])
            self._save()
        else:
            if raw.get("version") != VERSION:
                raise ImeError("unsupported state version")
            self.state = raw
            stored_key = raw.get("kdev")
            if master_key is not None:
                self._kdev = master_key
            elif stored_key:
                self._kdev = _unb64(stored_key)
            else:
                raise ImeError("device key required")
            self.state.setdefault("base", [])
            self.state.setdefault("local", {"current": None, "disabled": {}})
            self._base_cache = self._base_trans(self.state["base"])
            self._model = self._open_model()
            self._receipts = {mid: self._open_receipt(mid, r) for mid, r in raw.get("receipts", {}).items()}

    # ------------------------------------------------------------------ keys
    @staticmethod
    def _token_hash(token: str) -> str:
        return seal.token_hash(token)

    @staticmethod
    def _member_key(token: str) -> bytes:
        # Token is a 256-bit uniform secret; a domain-separated HMAC is a sound
        # KDF (no need for password stretching).
        return hmac.new(token.encode("utf-8"), b"synthime-lkey-v1", hashlib.sha256).digest()

    @staticmethod
    def _mid_for(th: str) -> str:
        # Pseudonymous id from a one-way token hash: reveals no name.
        return "u" + th[:12]

    # ------------------------------------------------------------- (de)sealing
    def _open_model(self) -> Dict[str, Dict[str, int]]:
        blob = self.state.get("sealed_model")
        if not blob:
            return {}
        plain = seal.unseal(self._kdev, _unb64(blob), b"model")
        return json.loads(_unpad(plain).decode("utf-8"))

    def _open_receipt(self, mid: str, rec: dict) -> dict:
        aad = ("receipt|%s|%d" % (mid, rec["epoch"])).encode("ascii")
        plain = seal.unseal(self._kdev, _unb64(rec["blob"]), aad)
        return json.loads(_unpad(plain).decode("utf-8"))

    def _save(self) -> None:
        self.state["sealed_model"] = _b64(seal.seal(self._kdev, _pad(_canon(self._model)), b"model"))
        receipts = {}
        for mid, data in self._receipts.items():
            epoch = data["epoch"]
            aad = ("receipt|%s|%d" % (mid, epoch)).encode("ascii")
            receipts[mid] = {
                "epoch": epoch,
                "blob": _b64(seal.seal(self._kdev, _pad(_canon(data)), aad)),
            }
        self.state["receipts"] = receipts
        self.store.save(self.state)

    # --------------------------------------------------------------- members
    def _find_member(self, token: str) -> Optional[str]:
        th = self._token_hash(token)
        for mid, rec in self.state["members"].items():
            if rec.get("token_hash") == th:
                return mid
        return None

    def join(self, token: str) -> str:
        """Enrol a member (idempotent).  Returns the pseudonymous member id."""
        mid = self._find_member(token)
        if mid is not None:
            return mid
        th = self._token_hash(token)
        revoked = self.state["revoked"].get(th)
        epoch = (revoked["last_epoch"] if revoked else 0) + 1
        mid = self._mid_for(th)
        # Guard against the vanishingly unlikely prefix collision.
        while mid in self.state["members"] and self.state["members"][mid]["token_hash"] != th:
            mid = "u" + hashlib.sha256(mid.encode()).hexdigest()[:12]
        self.state["members"][mid] = {"token_hash": th, "epoch": epoch}
        self._receipts[mid] = {"epoch": epoch, "sentences": []}
        self._save()
        return mid

    def member_id(self, token: str) -> Optional[str]:
        return self._find_member(token)

    def members(self) -> List[str]:
        return sorted(self.state["members"])

    # ---------------------------------------------------------------- learn
    def learn(self, token: str, sentences: Iterable[str]) -> int:
        """Teach sentences for ``token``'s member; returns records added.

        Identical sentences re-taught by the same member are de-duplicated (a
        member's copy counts once), but the same sentence taught by a different
        member adds that member's own count.
        """
        mid = self.join(token)
        return self._learn_mid(mid, sentences)

    def _learn_mid(self, mid: str, sentences: Iterable[str]) -> int:
        data = self._receipts[mid]
        existing = set(data["sentences"])
        added = 0
        for raw in sentences:
            sid = normalize_sentence(raw)
            if sid in existing:
                continue
            existing.add(sid)
            data["sentences"].append(sid)
            self._add_transitions(sentence_transitions(sid), +1)
            added += 1
        if added:
            self._save()
        return added

    def unlearn(self, token: str, sentence: str) -> int:
        """Remove ``sentence`` from this member's contribution."""
        mid = self._find_member(token)
        if mid is None:
            raise AuthError("unknown member")
        sid = normalize_sentence(sentence)
        data = self._receipts[mid]
        if sid not in data["sentences"]:
            return 0
        data["sentences"].remove(sid)
        self._add_transitions(sentence_transitions(sid), -1)
        self._save()
        return 1

    def replace_sentence(self, token: str, old: str, new: str) -> int:
        """Reteach: change one of this member's sentences into another."""
        removed = self.unlearn(token, old)
        self.learn(token, [new])
        return removed

    # ---------------------------------------------------------------- leaving
    def exit(self, token: str) -> str:
        """Withdraw the member owning ``token`` (idempotent)."""
        mid = self._find_member(token)
        if mid is None:
            th = self._token_hash(token)
            if th in self.state["revoked"]:
                return "already-revoked"
            raise AuthError("unknown member")
        return self._revoke_mid(mid)

    def revoke(self, member_id: str) -> str:
        """Withdraw by member id, without the member's token.

        This is the "the family member already walked off with their library"
        path: the device still holds a sealed compensating receipt, so it can
        subtract the contribution exactly.  Authorise this at the application
        layer if needed.
        """
        if member_id not in self.state["members"]:
            return "already-revoked"
        return self._revoke_mid(member_id)

    def _revoke_mid(self, mid: str) -> str:
        member = self.state["members"][mid]
        th = member["token_hash"]
        epoch = member["epoch"]
        data = self._receipts.get(mid, {"epoch": epoch, "sentences": []})
        for sid in data["sentences"]:
            self._add_transitions(sentence_transitions(sid), -1)
        del self.state["members"][mid]
        self._receipts.pop(mid, None)
        prior = self.state["revoked"].get(th, {"last_epoch": 0})["last_epoch"]
        last = max(prior, epoch)
        self.state["revoked"][th] = {"last_epoch": last}
        self._save()
        return "revoked"

    # ------------------------------------------------------- backup / restore
    def member_backup(self, token: str) -> dict:
        """Export a member's learning material, sealed under their own token.

        Portable between devices and unreadable without the token.
        """
        mid = self._find_member(token)
        if mid is None:
            raise AuthError("unknown member")
        epoch = self.state["members"][mid]["epoch"]
        sids = list(self._receipts[mid]["sentences"])
        lkey = self._member_key(token)
        blob = seal.seal(lkey, _pad(_canon({"sentences": sorted(sids)})), b"bundle")
        return {"epoch": epoch, "token_hash": self.state["members"][mid]["token_hash"], "blob": _b64(blob)}

    def member_restore(self, token: str, bundle: dict, replace: bool = False) -> int:
        """Restore a member's learning material from :meth:`member_backup`.

        Refuses to revive a revoked epoch (replay protection).  ``replace``
        swaps the member's current sentences for the bundle's.
        """
        th = self._token_hash(token)
        if th != bundle.get("token_hash"):
            raise AuthError("token does not match bundle")
        epoch = int(bundle["epoch"])
        revoked = self.state["revoked"].get(th)
        if revoked and epoch <= revoked["last_epoch"]:
            raise ReplayError("epoch already revoked; contribution stays withdrawn")
        lkey = self._member_key(token)
        plain = seal.unseal(lkey, _unb64(bundle["blob"]), b"bundle")
        sids = json.loads(_unpad(plain).decode("utf-8"))["sentences"]
        mid = self._find_member(token)
        if mid is None:
            # Adopt the bundle's epoch directly (not join()'s epoch 1), so a
            # device that has already revoked a higher epoch keeps refusing it.
            mid = self._mid_for(th)
            self.state["members"][mid] = {"token_hash": th, "epoch": epoch}
            self._receipts[mid] = {"epoch": epoch, "sentences": []}
        elif self.state["members"][mid]["epoch"] != epoch:
            raise ImeError("epoch mismatch on restore")
        if replace:
            for sid in list(self._receipts[mid]["sentences"]):
                self.unlearn(token, sid)
        return self._learn_mid(mid, sids)

    # ------------------------------------------------------------ base corpus
    def set_base(self, sentences: Iterable[str]) -> int:
        self.state["base"] = [normalize_sentence(s) for s in sentences]
        self._base_cache = self._base_trans(self.state["base"])
        self._save()
        return len(self.state["base"])

    def add_base(self, sentences: Iterable[str]) -> int:
        for s in sentences:
            self.state["base"].append(normalize_sentence(s))
        self._base_cache = self._base_trans(self.state["base"])
        self._save()
        return len(self.state["base"])

    @staticmethod
    def _base_trans(sentences: Iterable[str]) -> Dict[str, Dict[str, int]]:
        trans: Dict[str, Dict[str, int]] = {}
        for sid in sentences:
            for (p, n), c in sentence_transitions(sid).items():
                trans.setdefault(p, {})
                trans[p][n] = trans[p].get(n, 0) + c
        return trans

    # ------------------------------------------------------------ local words
    def set_current_user(self, name: Optional[str]) -> None:
        """Select whose local disabled-word list applies (device-local state)."""
        self.state["local"]["current"] = name or ""
        self._save()

    def _local_list(self) -> List[str]:
        cur = self.state["local"].get("current") or ""
        return self.state["local"]["disabled"].setdefault(cur, [])

    def local_disable(self, word: str) -> None:
        d = self._local_list()
        if word not in d:
            d.append(word)
            d.sort()
            self._save()

    def local_enable(self, word: str) -> None:
        d = self._local_list()
        if word in d:
            d.remove(word)
            self._save()

    def local_disabled(self) -> Set[str]:
        return set(self._local_list())

    # --------------------------------------------------------------- counts
    def _model_add(self, p: str, n: str, delta: int) -> None:
        row = self._model.setdefault(p, {})
        v = row.get(n, 0) + delta
        if v <= 0:
            row.pop(n, None)
        else:
            row[n] = v
        if not row:
            self._model.pop(p, None)

    def _add_transitions(self, counts: Dict[Tuple[str, str], int], sign: int) -> None:
        for (p, n), c in counts.items():
            self._model_add(p, n, sign * c)

    def counts_for(self, prefix: str) -> Dict[str, int]:
        """Owner-free merged next-word counts for ``prefix``.

        Base-corpus counts and all members' live counts are summed.  Whole-word
        prefixes predict the next word; a trailing partial word filters to
        completions of the current word.
        """
        tokens = prefix.split()
        at_boundary = prefix == "" or prefix[-1:].isspace()
        if at_boundary:
            prev = tokens[-1] if tokens else START
            partial = ""
        else:
            prev = tokens[-2] if len(tokens) >= 2 else START
            partial = tokens[-1]
        counts: Dict[str, int] = {}
        for table in (self._base_cache, self._model):
            for n, c in table.get(prev, {}).items():
                if partial and not n.startswith(partial):
                    continue
                counts[n] = counts.get(n, 0) + c
        return {w: counts[w] for w in sorted(counts)}

    def suggest(self, prefix: str, forbid: Iterable[str] = ()) -> List[str]:
        """Ordered next-word candidates after ``prefix``.

        Order is ``(count desc, word asc)`` — owner-free and content-derived,
        so no per-member or recency signal leaks.  ``forbid`` (plus the current
        user's local disabled list) only filters the result, never the counts.
        """
        forbidden = set(forbid) | self.local_disabled()
        counts = self.counts_for(prefix)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [w for w, _ in ranked if w not in forbidden]

    def chain(self, prefix: str, steps: int, forbid: Iterable[str] = ()) -> List[str]:
        """Multi-step association: repeatedly extend with the top suggestion."""
        forbidden = set(forbid) | self.local_disabled()
        out: List[str] = []
        cur = prefix
        if cur and not cur[-1:].isspace():
            cur = " ".join(cur.split()) + " "
        for _ in range(steps):
            words = [w for w in self.suggest(cur, forbidden)]
            if not words:
                break
            w = words[0]
            out.append(w)
            cur = cur + w + " "
        return out

    # ------------------------------------------------------------- introspection
    def member_sentences(self, member_id: str) -> List[str]:
        """Decrypted sentences for one member (device-internal / tests).

        Requires the device key, which lives on the device; this is why ordinary
        users of the device file cannot enumerate authorship.
        """
        data = self._receipts.get(member_id)
        return sorted(data["sentences"]) if data else []

    def vocabulary(self) -> List[str]:
        words: Set[str] = set()
        for table in (self._base_cache, self._model):
            for p, row in table.items():
                if p:
                    words.add(p)
                words.update(row)
        return sorted(words)
