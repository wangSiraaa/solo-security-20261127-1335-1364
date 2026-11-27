"""Restartable local escrow vault ("代存处") for a paired fair exchange.

The vault:

* holds only the parties' **public keys** and the **ciphertext** of each
  sealed packet (plus a packet key sealed *to the recipient*), so it is
  structurally unable to read a packet or sign an unsealing receipt on a
  party's behalf;
* proves, per deposit, which version was completely placed in the cabinet,
  to whom it was addressed, and until which tick it is retained;
* creates both parties' claim rights in one transaction (paired effectiveness)
  or not at all -- nobody can read the other's original before their own is
  irrevocably committed;
* binds authorisations to the exact (a_version, b_version) id+digest pair, so a
  re-uploaded / tampered packet can never ride an old authorisation;
* refuses to record an "opened" event whose receipt rests on an expired claim
  credential; and
* writes an append-only ledger that separates *when each party acquired the
  right* from *who actually opened which version*.

All logical time is integer *ticks* supplied by the caller, so the same cabinet
facts can be replayed under different settlement policies.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
from typing import Any, Dict, List, Optional

from . import seal
from .crypto import canonical_json, encrypt, sha256_hex, verify_payload

# order states
COLLECTING = "COLLECTING"   # not both versions present yet
PENDING = "PENDING"         # both versions present, no rights yet
EFFECTIVE = "EFFECTIVE"     # paired effectiveness happened, claim rights exist
EXPIRED = "EXPIRED"         # retention window fully elapsed after effectiveness
ABORTED = "ABORTED"         # settler closed the order with no exchange
TERMINAL = (EFFECTIVE, EXPIRED, ABORTED)

# settlement policies
POLICY_NONE = "NONE"        # only a pair of final confirmations settles
EARLY = "EARLY_LOCK"        # a pair of delegations may lock immediately
HOLD = "HOLD_FOR_CONFIRMATION"  # reserve a window; defer delegations

# what the settler does when a hold window elapses without confirmation
EXPIRE_ABORT = "ABORT"           # close with no exchange
EXPIRE_FALLBACK_LOCK = "FALLBACK_LOCK"  # lock then, if both delegations remain valid

# authorisation kinds
DELEGATION = "DELEGATION"      # "even if I am offline, locking both is allowed"
CONFIRMATION = "CONFIRMATION"  # final sign-off while online

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parties(
    party_id TEXT PRIMARY KEY,
    public_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
    order_id TEXT PRIMARY KEY,
    party_a TEXT NOT NULL,
    party_b TEXT NOT NULL,
    retention_ticks INTEGER NOT NULL,
    state TEXT NOT NULL,
    effective_tick INTEGER,
    retained_until INTEGER,
    hold_until INTEGER,
    policy TEXT NOT NULL DEFAULT 'NONE',
    expiry_action TEXT NOT NULL DEFAULT 'ABORT',
    created_tick INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    party TEXT NOT NULL,
    seq INTEGER NOT NULL,
    digest TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    envelope TEXT NOT NULL,
    recipient TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    retained_until INTEGER NOT NULL,
    withdrawn INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auths(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    party TEXT NOT NULL,
    kind TEXT NOT NULL,
    a_ver INTEGER NOT NULL,
    b_ver INTEGER NOT NULL,
    a_digest TEXT NOT NULL,
    b_digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    sig TEXT NOT NULL,
    tick INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tokens(
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    party TEXT NOT NULL,
    read_ver INTEGER NOT NULL,
    read_digest TEXT NOT NULL,
    issued_tick INTEGER NOT NULL,
    expiry_tick INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opens(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    party TEXT NOT NULL,
    token_id TEXT NOT NULL,
    read_ver INTEGER NOT NULL,
    digest TEXT NOT NULL,
    tick INTEGER NOT NULL,
    receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    event TEXT NOT NULL,
    party TEXT,
    ver INTEGER,
    digest TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_versions_order ON versions(order_id);
CREATE INDEX IF NOT EXISTS idx_ledger_order ON ledger(order_id, seq);
"""


class VaultError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code

    def __repr__(self) -> str:
        return f"VaultError({self.code!r}, {str(self)!r})"


class Vault:
    """A single SQLite file holds all state; reopening the same path resumes."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Vault":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transaction helper --------------------------------------------------

    @contextlib.contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    # -- small helpers -------------------------------------------------------

    def _ledger(self, order_id: str, tick: int, event: str, party: Optional[str] = None,
                ver: Optional[int] = None, digest: Optional[str] = None,
                detail: Any = None) -> None:
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS s FROM ledger WHERE order_id=?", (order_id,)
        ).fetchone()["s"]
        self.conn.execute(
            "INSERT INTO ledger(order_id,seq,tick,event,party,ver,digest,detail) VALUES(?,?,?,?,?,?,?,?)",
            (order_id, seq, tick, event, party, ver, digest,
             canonical_json(detail) if detail is not None else None),
        )

    def _order(self, order_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None:
            raise VaultError("NO_ORDER", f"unknown order {order_id}")
        return row

    def _party_pub(self, party: str) -> Dict[str, Any]:
        row = self.conn.execute("SELECT public_key FROM parties WHERE party_id=?", (party,)).fetchone()
        if row is None:
            raise VaultError("NO_PARTY", f"unknown party {party}")
        return json.loads(row["public_key"])

    def _other(self, order: sqlite3.Row, party: str) -> str:
        a, b = order["party_a"], order["party_b"]
        if party == a:
            return b
        if party == b:
            return a
        raise VaultError("NOT_IN_ORDER", f"{party} is not a party to {order['order_id']}")

    def _current_version(self, order_id: str, party: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM versions WHERE order_id=? AND party=? AND withdrawn=0 "
            "ORDER BY seq DESC LIMIT 1",
            (order_id, party),
        ).fetchone()

    def _version_by_id(self, order_id: str, party: str, ver: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM versions WHERE order_id=? AND party=? AND id=? AND withdrawn=0",
            (order_id, party, ver),
        ).fetchone()

    # -- registration --------------------------------------------------------

    def register_party(self, party_id: str, public_key: Dict[str, Any]) -> None:
        with self._tx():
            if self.conn.execute("SELECT 1 FROM parties WHERE party_id=?", (party_id,)).fetchone():
                raise VaultError("PARTY_EXISTS", party_id)
            self.conn.execute(
                "INSERT INTO parties(party_id,public_key) VALUES(?,?)",
                (party_id, canonical_json(public_key).decode()),
            )

    def open_order(self, order_id: str, party_a: str, party_b: str,
                   retention_ticks: int, tick: int) -> None:
        with self._tx():
            if self.conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone():
                raise VaultError("ORDER_EXISTS", order_id)
            if party_a == party_b:
                raise VaultError("SELF_EXCHANGE", "a party cannot exchange with itself")
            if retention_ticks <= 0:
                raise VaultError("BAD_RETENTION", "retention must be positive")
            self._party_pub(party_a)
            self._party_pub(party_b)
            self.conn.execute(
                "INSERT INTO orders(order_id,party_a,party_b,retention_ticks,state,created_tick) "
                "VALUES(?,?,?,?,?,?)",
                (order_id, party_a, party_b, retention_ticks, COLLECTING, tick),
            )
            self._ledger(order_id, tick, "ORDER_OPENED", detail={
                "party_a": party_a, "party_b": party_b, "retention_ticks": retention_ticks,
            })

    def set_policy(self, order_id: str, policy: str, tick: int,
                   hold_ticks: Optional[int] = None,
                   expiry_action: str = EXPIRE_ABORT) -> None:
        if policy not in (POLICY_NONE, EARLY, HOLD):
            raise VaultError("BAD_POLICY", policy)
        if expiry_action not in (EXPIRE_ABORT, EXPIRE_FALLBACK_LOCK):
            raise VaultError("BAD_EXPIRY_ACTION", expiry_action)
        with self._tx():
            order = self._order(order_id)
            if order["state"] in TERMINAL:
                raise VaultError("ALREADY_SETTLED", "cannot change policy after settlement")
            hold_until = None
            if policy == HOLD:
                if hold_ticks is None or hold_ticks < 0:
                    raise VaultError("BAD_HOLD", "hold policy needs a non-negative hold window")
                hold_until = tick + hold_ticks
            self.conn.execute(
                "UPDATE orders SET policy=?, hold_until=?, expiry_action=? WHERE order_id=?",
                (policy, hold_until, expiry_action, order_id),
            )
            self._ledger(order_id, tick, "POLICY_SET", detail={
                "policy": policy, "hold_until": hold_until, "expiry_action": expiry_action,
            })
            self._refresh_state(order_id, tick)

    # -- deposit / withdraw --------------------------------------------------

    def deposit(self, order_id: str, party: str, recipient: str,
                digest: str, ciphertext_hex: str, envelope_hex: str,
                tick: int) -> Dict[str, Any]:
        """Record a prepared sealed packet. The caller (the party's terminal) has
        already sealed the plaintext to the recipient; the vault only keeps
        ciphertext plus a key envelope addressed to the recipient."""
        order = self._order(order_id)
        if self._other(order, party) != recipient:
            raise VaultError("BAD_RECIPIENT",
                             "a packet must be addressed to the counterparty")
        if order["state"] in TERMINAL:
            raise VaultError("ALREADY_SETTLED",
                             "a pair already took effect; uploads cannot overwrite it")
        # verify the packet is *completely in the cabinet*: well-formed sealed
        # blob and a key envelope addressed to the recipient's public key.
        try:
            raw = bytes.fromhex(ciphertext_hex)
        except ValueError:
            raise VaultError("BAD_PACKET", "ciphertext is not a hex blob")
        if len(raw) < seal.NONCE_LEN + seal.TAG_LEN + 1:
            raise VaultError("BAD_PACKET", "sealed packet is truncated")
        if not isinstance(digest, str) or len(digest) != 64:
            raise VaultError("BAD_DIGEST", "digest must be a sha256 hex string")
        try:
            bytes.fromhex(envelope_hex)
        except ValueError:
            raise VaultError("BAD_ENVELOPE", "key envelope is not a hex blob")
        with self._tx():
            seq = self.conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS s FROM versions WHERE order_id=? AND party=?",
                (order_id, party),
            ).fetchone()["s"]
            cur = self._current_version(order_id, party)
            if cur is not None:
                self.conn.execute("UPDATE versions SET withdrawn=1 WHERE id=?", (cur["id"],))
                self._ledger(order_id, tick, "VERSION_SUPERSEDED", party, cur["id"], cur["digest"])
                # any authorisation naming the superseded version no longer applies
                self._revoke_auths_naming(order_id, cur["id"], tick)
            ins = self.conn.execute(
                "INSERT INTO versions(order_id,party,seq,digest,ciphertext,envelope,recipient,"
                "created_tick,retained_until) VALUES(?,?,?,?,?,?,?,?,?)",
                (order_id, party, seq, digest, ciphertext_hex, envelope_hex, recipient,
                 tick, tick + order["retention_ticks"]),
            )
            self._ledger(order_id, tick, "DEPOSITED", party, ins.lastrowid, digest, {
                "seq": seq, "recipient": recipient,
                "retained_until": tick + order["retention_ticks"],
            })
            self._refresh_state(order_id, tick)
            return {
                "order_id": order_id, "party": party, "ver": ins.lastrowid,
                "seq": seq, "digest": digest, "recipient": recipient,
                "retained_until": tick + order["retention_ticks"],
            }

    def withdraw(self, order_id: str, party: str, tick: int,
                 ver: Optional[int] = None) -> None:
        """Withdraw the current deposit. Allowed whenever the pair has *not* taken
        effect -- including after an abort, since no rights were ever created."""
        with self._tx():
            order = self._order(order_id)
            self._other(order, party)
            if order["state"] in (EFFECTIVE, EXPIRED):
                raise VaultError("ALREADY_SETTLED",
                                 "cannot withdraw after paired effectiveness")
            cur = self._current_version(order_id, party)
            if cur is None:
                raise VaultError("NO_SUCH_VERSION", "nothing deposited to withdraw")
            if ver is not None and ver != cur["id"]:
                raise VaultError("NOT_CURRENT", "only the current version may be withdrawn")
            self.conn.execute("UPDATE versions SET withdrawn=1 WHERE id=?", (cur["id"],))
            self._ledger(order_id, tick, "WITHDRAWN", party, cur["id"], cur["digest"])
            self._revoke_auths_naming(order_id, cur["id"], tick)
            self._refresh_state(order_id, tick)

    def override_version_digest(self, order_id: str, party: str, new_digest: str,
                                tick: int) -> None:
        """Diagnostic hook (tampered cabinet entry): point the current version row
        at a different digest *in place*. Stored authorisations remain bound to
        the old digest, so none of them may settle the new content."""
        with self._tx():
            order = self._order(order_id)
            self._other(order, party)
            if order["state"] in TERMINAL:
                raise VaultError("ALREADY_SETTLED", "cannot tamper with an effective version")
            cur = self._current_version(order_id, party)
            if cur is None:
                raise VaultError("NO_SUCH_VERSION", "nothing to override")
            self.conn.execute("UPDATE versions SET digest=? WHERE id=?", (new_digest, cur["id"]))
            self._ledger(order_id, tick, "VERSION_OVERRIDDEN", party, cur["id"], new_digest,
                         {"old_digest": cur["digest"]})
            self._refresh_state(order_id, tick)

    def _revoke_auths_naming(self, order_id: str, ver: int, tick: int) -> None:
        rows = self.conn.execute(
            "SELECT * FROM auths WHERE order_id=? AND revoked=0 AND (a_ver=? OR b_ver=?)",
            (order_id, ver, ver),
        ).fetchall()
        for r in rows:
            self.conn.execute("UPDATE auths SET revoked=1 WHERE id=?", (r["id"],))
            self._ledger(order_id, tick, "AUTH_REVOKED", r["party"], None, None,
                         {"auth_id": r["id"], "kind": r["kind"],
                          "reason": "named version superseded"})

    # -- authorisations ------------------------------------------------------

    def _valid_auths(self, order_id: str, kind: str, va: sqlite3.Row,
                     vb: sqlite3.Row) -> Dict[str, sqlite3.Row]:
        """Non-revoked, signature-verified authorisations that name *exactly*
        the current pair (same ids and same digests)."""
        rows = self.conn.execute(
            "SELECT * FROM auths WHERE order_id=? AND kind=? AND revoked=0",
            (order_id, kind),
        ).fetchall()
        out: Dict[str, sqlite3.Row] = {}
        for r in rows:
            payload = json.loads(r["payload"])
            if (r["a_ver"] != va["id"] or r["b_ver"] != vb["id"]
                    or r["a_digest"] != va["digest"] or r["b_digest"] != vb["digest"]):
                continue
            if (payload.get("a_ver") != va["id"] or payload.get("b_ver") != vb["id"]
                    or payload.get("a_digest") != va["digest"]
                    or payload.get("b_digest") != vb["digest"]):
                continue
            if not verify_payload({"payload": payload, "sig": r["sig"]},
                                  self._party_pub(r["party"])):
                continue
            out[r["party"]] = r
        return out

    def submit_auth(self, order_id: str, party: str, signed: Dict[str, Any],
                    tick: int) -> None:
        """Record a signed delegation or confirmation. The signed payload must
        name the currently stored version ids *and digests*."""
        if not verify_payload(signed, self._party_pub(party)):
            raise VaultError("BAD_SIGNATURE", "authorisation signature does not verify")
        payload = signed["payload"]
        kind = payload.get("kind")
        if kind not in (DELEGATION, CONFIRMATION):
            raise VaultError("BAD_AUTH_KIND", kind)
        with self._tx():
            order = self._order(order_id)
            if order["state"] in TERMINAL:
                raise VaultError("ALREADY_SETTLED", "order already settled")
            if payload.get("party") != party:
                raise VaultError("PAYLOAD_MISMATCH", "payload party mismatch")
            if payload.get("order_id") != order_id:
                raise VaultError("PAYLOAD_MISMATCH", "payload order mismatch")
            if self._other(order, party) != payload.get("counterparty"):
                raise VaultError("BAD_COUNTERPARTY", "counterparty mismatch")
            a_ver = payload.get("a_ver")
            b_ver = payload.get("b_ver")
            va = self._version_by_id(order_id, order["party_a"], a_ver) \
                if isinstance(a_ver, int) else None
            vb = self._version_by_id(order_id, order["party_b"], b_ver) \
                if isinstance(b_ver, int) else None
            if va is None or vb is None:
                raise VaultError("NO_SUCH_PAIR", "both named versions must be current")
            if payload.get("a_digest") != va["digest"] or payload.get("b_digest") != vb["digest"]:
                raise VaultError("DIGEST_MISMATCH",
                                 "payload digests do not match the current cabinet versions")
            self.conn.execute(
                "INSERT INTO auths(order_id,party,kind,a_ver,b_ver,a_digest,b_digest,payload,sig,tick) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (order_id, party, kind, a_ver, b_ver, va["digest"], vb["digest"],
                 canonical_json(payload).decode(), signed["sig"], tick),
            )
            self._ledger(order_id, tick, "AUTH_SUBMITTED", party, None, None, {
                "kind": kind, "a_ver": a_ver, "b_ver": b_ver,
                "a_digest": va["digest"], "b_digest": vb["digest"],
            })
            self._refresh_state(order_id, tick)

    def revoke_auth(self, order_id: str, party: str, kind: str,
                    a_ver: int, b_ver: int, tick: int) -> bool:
        """Retract a delegation/confirmation the party itself gave, before
        effectiveness."""
        with self._tx():
            order = self._order(order_id)
            self._other(order, party)
            if order["state"] in TERMINAL:
                raise VaultError("ALREADY_SETTLED", "cannot revoke after paired effectiveness")
            rows = self.conn.execute(
                "SELECT * FROM auths WHERE order_id=? AND party=? AND kind=? "
                "AND a_ver=? AND b_ver=? AND revoked=0",
                (order_id, party, kind, a_ver, b_ver),
            ).fetchall()
            for r in rows:
                self.conn.execute("UPDATE auths SET revoked=1 WHERE id=?", (r["id"],))
                self._ledger(order_id, tick, "AUTH_REVOKED", party, None, None,
                             {"auth_id": r["id"], "kind": kind, "reason": "party recall"})
            self._refresh_state(order_id, tick)
            return bool(rows)

    # -- state machine -------------------------------------------------------

    def _refresh_state(self, order_id: str, tick: int) -> None:
        order = self._order(order_id)
        if order["state"] in TERMINAL:
            return
        va = self._current_version(order_id, order["party_a"])
        vb = self._current_version(order_id, order["party_b"])
        if va is None or vb is None:
            if order["state"] == PENDING:
                self.conn.execute("UPDATE orders SET state=? WHERE order_id=?",
                                  (COLLECTING, order_id))
                self._ledger(order_id, tick, "PAIR_INCOMPLETE")
            return
        if order["state"] == COLLECTING:
            self.conn.execute("UPDATE orders SET state=? WHERE order_id=?",
                              (PENDING, order_id))
            self._ledger(order_id, tick, "BOTH_PRESENT", detail={
                "a_ver": va["id"], "b_ver": vb["id"],
                "a_digest": va["digest"], "b_digest": vb["digest"],
            })
        # 1) both final confirmations -> mandatory settlement, any policy
        confirmations = self._valid_auths(order_id, CONFIRMATION, va, vb)
        if all(p in confirmations for p in (order["party_a"], order["party_b"])):
            self._settle(order_id, tick, reason="BOTH_CONFIRMED")
            return
        policy = order["policy"]
        # 2) EARLY policy: a pair of live delegations may lock now
        if policy == EARLY:
            delegations = self._valid_auths(order_id, DELEGATION, va, vb)
            if all(p in delegations for p in (order["party_a"], order["party_b"])):
                self._settle(order_id, tick, reason="DELEGATION_LOCK")
                return
        # 3) HOLD policy: on expiry the settler's pre-chosen action runs
        if policy == HOLD and order["hold_until"] is not None and tick >= order["hold_until"]:
            if order["expiry_action"] == EXPIRE_FALLBACK_LOCK:
                delegations = self._valid_auths(order_id, DELEGATION, va, vb)
                if all(p in delegations for p in (order["party_a"], order["party_b"])):
                    self._settle(order_id, tick, reason="HOLD_EXPIRY_FALLBACK_LOCK")
                    return
            self.conn.execute("UPDATE orders SET state=? WHERE order_id=?", (ABORTED, order_id))
            self._ledger(order_id, tick, "HOLD_EXPIRED_ABORTED", detail={
                "hold_until": order["hold_until"],
            })

    def _settle(self, order_id: str, tick: int, reason: str) -> None:
        """Paired effectiveness: create both claim rights in one transaction."""
        order = self._order(order_id)
        va = self._current_version(order_id, order["party_a"])
        vb = self._current_version(order_id, order["party_b"])
        retained_until = tick + order["retention_ticks"]
        # retention guarantee runs from effectiveness and applies to both packets
        self.conn.execute("UPDATE versions SET retained_until=? WHERE id IN (?,?)",
                          (retained_until, va["id"], vb["id"]))
        self.conn.execute(
            "UPDATE orders SET state=?, effective_tick=?, retained_until=? WHERE order_id=?",
            (EFFECTIVE, tick, retained_until, order_id),
        )
        # paired claim tokens: A may read B's version, B may read A's
        ta = secrets.token_hex(16)
        tb = secrets.token_hex(16)
        self.conn.execute(
            "INSERT INTO tokens(id,order_id,party,read_ver,read_digest,issued_tick,expiry_tick) "
            "VALUES(?,?,?,?,?,?,?)",
            (ta, order_id, order["party_a"], vb["id"], vb["digest"], tick, retained_until),
        )
        self.conn.execute(
            "INSERT INTO tokens(id,order_id,party,read_ver,read_digest,issued_tick,expiry_tick) "
            "VALUES(?,?,?,?,?,?,?)",
            (tb, order_id, order["party_b"], va["id"], va["digest"], tick, retained_until),
        )
        self._ledger(order_id, tick, "PAIR_EFFECTIVE", detail={
            "reason": reason,
            "a_ver": va["id"], "b_ver": vb["id"],
            "a_digest": va["digest"], "b_digest": vb["digest"],
            "retained_until": retained_until,
        })
        # per-party right acquisition, same tick, distinguishes when each got it
        self._ledger(order_id, tick, "RIGHT_EFFECTIVE", order["party_a"], vb["id"],
                     vb["digest"], {"right": "may read counterparty b", "token": ta})
        self._ledger(order_id, tick, "RIGHT_EFFECTIVE", order["party_b"], va["id"],
                     va["digest"], {"right": "may read counterparty a", "token": tb})
        self._ledger(order_id, tick, "TOKENS_ISSUED", detail={
            "expiry_tick": retained_until, "tokens": [ta, tb],
        })

    def advance(self, order_id: str, tick: int) -> Dict[str, Any]:
        """Move logical time forward: settle if now permitted, resolve a hold
        that has elapsed, and mark full retention expiry."""
        with self._tx():
            order = self._order(order_id)
            if order["state"] not in (EFFECTIVE, EXPIRED, ABORTED):
                self._refresh_state(order_id, tick)
            order = self._order(order_id)
            if (order["state"] == EFFECTIVE and order["retained_until"] is not None
                    and tick > order["retained_until"]):
                self.conn.execute("UPDATE orders SET state=? WHERE order_id=?",
                                  (EXPIRED, order_id))
                self._ledger(order_id, tick, "RIGHTS_EXPIRED", detail={
                    "retained_until": order["retained_until"],
                })
            return self.status(order_id, requester=None, tick=tick)

    # -- claim / open --------------------------------------------------------

    def _get_token(self, order_id: str, party: str, token_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM tokens WHERE id=? AND order_id=? AND party=?",
            (token_id, order_id, party),
        ).fetchone()
        if row is None:
            raise VaultError("NO_TOKEN", "no such claim token for this party/order")
        return row

    def claim(self, order_id: str, party: str, token_id: str, tick: int) -> Dict[str, Any]:
        """Release the material for the counterparty's sealed packet, provided the
        claim token is valid now. The vault cannot decrypt it."""
        order = self._order(order_id)
        if order["state"] != EFFECTIVE:
            raise VaultError("NOT_EFFECTIVE", "no claim rights exist yet")
        tok = self._get_token(order_id, party, token_id)
        if tick > tok["expiry_tick"]:
            raise VaultError("TOKEN_EXPIRED", "claim credential has expired")
        if tick < tok["issued_tick"]:
            raise VaultError("TOKEN_NOT_YET_VALID", "claim credential not yet valid")
        other = self._other(order, party)
        ver = self._version_by_id(order_id, other, tok["read_ver"])
        if ver is None or ver["id"] != tok["read_ver"] or ver["digest"] != tok["read_digest"]:
            raise VaultError("VERSION_MISMATCH", "claim token does not match a live version")
        self._ledger(order_id, tick, "CLAIM_RELEASED", party, ver["id"], ver["digest"],
                     {"token": token_id})
        return {
            "order_id": order_id, "party": party, "token_id": token_id,
            "read_ver": ver["id"], "digest": ver["digest"],
            "ciphertext": ver["ciphertext"], "envelope": ver["envelope"],
            "recipient": ver["recipient"], "expiry_tick": tok["expiry_tick"],
            "tick": tick,
        }

    def report_open(self, order_id: str, party: str, token_id: str, read_ver: int,
                    digest: str, signed_receipt: Dict[str, Any], tick: int) -> Dict[str, Any]:
        """Record that a party's own terminal opened a version, backed by a
        receipt signed by that party. An expired claim credential cannot back
        a recorded read."""
        order = self._order(order_id)
        if order["state"] not in (EFFECTIVE, EXPIRED):
            raise VaultError("NOT_EFFECTIVE", "no effective exchange")
        # the receipt must be signed by the party itself -- the vault only has
        # the public key, so it cannot fabricate this on the party's behalf.
        if not verify_payload(signed_receipt, self._party_pub(party)):
            raise VaultError("BAD_RECEIPT_SIGNATURE", "receipt not signed by party")
        rp = signed_receipt["payload"]
        if rp.get("order_id") != order_id or rp.get("party") != party:
            raise VaultError("RECEIPT_MISMATCH", "receipt order/party mismatch")
        if rp.get("token_id") != token_id or rp.get("read_ver") != read_ver \
                or rp.get("read_digest") != digest:
            raise VaultError("RECEIPT_MISMATCH", "receipt does not match token/version")
        tok = self._get_token(order_id, party, token_id)
        if order["state"] == EXPIRED or tick > tok["expiry_tick"]:
            self._ledger(order_id, tick, "OPEN_REJECTED", party, read_ver, digest,
                         {"token": token_id, "reason": "expired claim credential"})
            raise VaultError("TOKEN_EXPIRED",
                             "an expired claim credential cannot back a recorded read")
        if tok["read_ver"] != read_ver or tok["read_digest"] != digest:
            raise VaultError("VERSION_MISMATCH", "token does not cover this version")
        other = self._other(order, party)
        ver = self._version_by_id(order_id, other, read_ver)
        if ver is None or ver["digest"] != digest:
            raise VaultError("VERSION_MISMATCH", "no live version with this digest")
        with self._tx():
            self.conn.execute(
                "INSERT INTO opens(order_id,party,token_id,read_ver,digest,tick,receipt) "
                "VALUES(?,?,?,?,?,?,?)",
                (order_id, party, token_id, read_ver, digest, tick,
                 canonical_json(signed_receipt).decode()),
            )
            self._ledger(order_id, tick, "OPENED", party, read_ver, digest,
                         {"token": token_id})
        return {"order_id": order_id, "party": party, "read_ver": read_ver,
                "digest": digest, "tick": tick, "token_id": token_id}

    # -- queries -------------------------------------------------------------

    def status(self, order_id: str, requester: Optional[str] = None,
               tick: Optional[int] = None) -> Dict[str, Any]:
        order = self._order(order_id)
        va = self._current_version(order_id, order["party_a"])
        vb = self._current_version(order_id, order["party_b"])
        tokens = self.conn.execute(
            "SELECT id,party,read_ver,read_digest,issued_tick,expiry_tick "
            "FROM tokens WHERE order_id=?", (order_id,)
        ).fetchall()
        return {
            "order_id": order_id,
            "party_a": order["party_a"], "party_b": order["party_b"],
            "state": order["state"],
            "retention_ticks": order["retention_ticks"],
            "effective_tick": order["effective_tick"],
            "retained_until": order["retained_until"],
            "hold_until": order["hold_until"],
            "policy": order["policy"],
            "expiry_action": order["expiry_action"],
            "a_version": dict(va) if va else None,
            "b_version": dict(vb) if vb else None,
            "tokens": [dict(t) for t in tokens],
        }

    def ledger(self, order_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM ledger WHERE order_id=? ORDER BY seq", (order_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d["detail"] is not None:
                d["detail"] = json.loads(d["detail"])
            out.append(d)
        return out

    def opens(self, order_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM opens WHERE order_id=? ORDER BY id", (order_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["receipt"] = json.loads(d["receipt"])
            out.append(d)
        return out

    def version_history(self, order_id: str, party: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,seq,digest,recipient,created_tick,retained_until,withdrawn "
            "FROM versions WHERE order_id=? AND party=? ORDER BY seq",
            (order_id, party),
        ).fetchall()
        return [dict(r) for r in rows]
