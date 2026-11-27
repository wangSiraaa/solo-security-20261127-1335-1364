"""Convenience orchestration binding a :class:`Vault` to file-backed
:class:`Terminal`s. Used by the CLI, the scenario replays, and the tests.

Everything is persisted under a working directory so the whole exchange can be
stopped and resumed:
    <root>/vault.db            shared cabinet
    <root>/keys/<party>.json   each party's private key (terminal-local)
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .terminal import Terminal
from .vault import Vault, VaultError


class Session:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        os.makedirs(os.path.join(root, "keys"), exist_ok=True)
        self.vault = Vault(os.path.join(root, "vault.db"))

    def close(self) -> None:
        self.vault.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- identity ------------------------------------------------------------

    def _key_path(self, party: str) -> str:
        return os.path.join(self.root, "keys", f"{party}.json")

    def add_party(self, party: str, bits: int = 1024) -> Terminal:
        t = Terminal.create(party, bits)
        t.save(self._key_path(party))
        self.vault.register_party(party, t.public())
        return t

    def party(self, party: str) -> Terminal:
        return Terminal.load(party, self._key_path(party))

    def parties(self) -> List[str]:
        d = os.path.join(self.root, "keys")
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))

    def public_key(self, party: str) -> Dict:
        return self.vault._party_pub(party)

    # -- order lifecycle -----------------------------------------------------

    def open_order(self, order_id: str, party_a: str, party_b: str,
                   retention_ticks: int, tick: int) -> None:
        self.vault.open_order(order_id, party_a, party_b, retention_ticks, tick)

    def set_policy(self, order_id: str, policy: str, tick: int,
                   hold_ticks: Optional[int] = None,
                   expiry_action: str = "ABORT") -> None:
        self.vault.set_policy(order_id, policy, tick, hold_ticks, expiry_action)

    # -- deposit / withdraw --------------------------------------------------

    def deposit(self, order_id: str, party: str, plaintext: bytes, tick: int) -> Dict:
        recipient = self._counterparty(order_id, party)
        prep = self.party(party).prepare_deposit(self.public_key(recipient), plaintext)
        return self.vault.deposit(order_id, party, recipient, prep["digest"],
                                  prep["ciphertext"], prep["envelope"], tick)

    def withdraw(self, order_id: str, party: str, tick: int,
                 ver: Optional[int] = None) -> None:
        self.vault.withdraw(order_id, party, tick, ver)

    # -- authorisations ------------------------------------------------------

    def _counterparty(self, order_id: str, party: str) -> str:
        st = self.vault.status(order_id)
        if party == st["party_a"]:
            return st["party_b"]
        if party == st["party_b"]:
            return st["party_a"]
        raise VaultError("NOT_IN_ORDER", f"{party} not in {order_id}")

    def _current_pair(self, order_id: str):
        st = self.vault.status(order_id)
        va, vb = st["a_version"], st["b_version"]
        if va is None or vb is None:
            raise VaultError("NO_SUCH_PAIR", "both versions must be deposited")
        return st, va, vb

    def delegate(self, order_id: str, party: str, tick: int) -> None:
        st, va, vb = self._current_pair(order_id)
        signed = self.party(party).make_auth(
            "DELEGATION", order_id, self._counterparty(order_id, party),
            va["id"], vb["id"], va["digest"], vb["digest"])
        self.vault.submit_auth(order_id, party, signed, tick)

    def confirm(self, order_id: str, party: str, tick: int) -> None:
        st, va, vb = self._current_pair(order_id)
        signed = self.party(party).make_auth(
            "CONFIRMATION", order_id, self._counterparty(order_id, party),
            va["id"], vb["id"], va["digest"], vb["digest"])
        self.vault.submit_auth(order_id, party, signed, tick)

    def revoke_delegation(self, order_id: str, party: str, tick: int) -> bool:
        st, va, vb = self._current_pair(order_id)
        return self.vault.revoke_auth(order_id, party, "DELEGATION",
                                      va["id"], vb["id"], tick)

    def advance(self, order_id: str, tick: int) -> Dict:
        return self.vault.advance(order_id, tick)

    # -- claim / open --------------------------------------------------------

    def claim_token(self, order_id: str, party: str) -> Dict:
        st = self.vault.status(order_id)
        for t in st["tokens"]:
            if t["party"] == party:
                return t
        raise VaultError("NO_TOKEN", "no claim token for this party")

    def claim_and_open(self, order_id: str, party: str, tick: int) -> Dict:
        tok = self.claim_token(order_id, party)
        claim = self.vault.claim(order_id, party, tok["id"], tick)
        opened = self.party(party).open_claimed(claim)
        rec = self.vault.report_open(order_id, party, tok["id"], claim["read_ver"],
                                     claim["digest"], opened["receipt"], tick)
        rec["plaintext"] = opened["plaintext"]
        return rec

    def counterparty(self, order_id: str, party: str) -> str:
        return self._counterparty(order_id, party)

    def tamper_digest(self, order_id: str, party: str, new_digest: str, tick: int) -> None:
        """Diagnostic: replace the stored digest of the party's current version."""
        self.vault.override_version_digest(order_id, party, new_digest, tick)

    def open_raw(self, order_id: str, party: str, tick: int) -> Dict:
        """Open a claim and record the receipt (the normal read path)."""
        return self.claim_and_open(order_id, party, tick)

    def record_read(self, order_id: str, party: str, token_id: str, read_ver: int,
                    digest: str, plaintext: bytes, tick: int) -> Dict:
        """Build a party-signed receipt and attempt to record it. Used to test
        that an expired credential cannot back a recorded read."""
        terminal = self.party(party)
        receipt = terminal.sign_receipt(order_id, token_id, read_ver, digest, plaintext)
        return self.vault.report_open(order_id, party, token_id, read_ver, digest,
                                      receipt, tick)

    # -- queries -------------------------------------------------------------

    def status(self, order_id: str) -> Dict:
        return self.vault.status(order_id)

    def ledger(self, order_id: str) -> List[Dict]:
        return self.vault.ledger(order_id)
