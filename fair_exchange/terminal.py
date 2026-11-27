"""Terminal-side operations for one party ("各家终端").

Each party keeps its own RSA private key and seal/unseal operations.  The
vault only ever holds the party's public key, so:

* a sealed packet is sealed here, addressed to the counterparty's public key;
* an unsealing receipt ("凭据") is signed here, by the party that actually
  opened the packet -- the vault cannot sign it;
* a delegation / confirmation is signed here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from . import seal
from .crypto import (
    decrypt,
    encrypt,
    keygen,
    public_part,
    sha256_hex,
    sign_payload,
)


class Terminal:
    def __init__(self, party_id: str, key: Dict[str, Any]):
        self.party_id = party_id
        self.key = key

    # -- identity persistence (for restartability) ---------------------------

    @classmethod
    def create(cls, party_id: str, bits: int = 1024) -> "Terminal":
        return cls(party_id, keygen(bits))

    @classmethod
    def load(cls, party_id: str, path: str) -> "Terminal":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(party_id, data)

    def save(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.key, f)

    def public(self) -> Dict[str, Any]:
        return public_part(self.key)

    # -- sealed packet preparation -------------------------------------------

    def prepare_deposit(self, recipient_pub: Dict[str, Any], plaintext: bytes) -> Dict[str, str]:
        packet_key = os.urandom(32)
        ctext = seal.seal(plaintext, packet_key)
        envelope = encrypt(packet_key, recipient_pub)
        return {
            "digest": sha256_hex(plaintext),
            "ciphertext": ctext.hex(),
            "envelope": envelope,
        }

    # -- authorisations ------------------------------------------------------

    def make_auth(self, kind: str, order_id: str, counterparty: str,
                  a_ver: int, b_ver: int, a_digest: str, b_digest: str) -> Dict[str, Any]:
        payload = {
            "kind": kind,
            "order_id": order_id,
            "party": self.party_id,
            "counterparty": counterparty,
            "a_ver": a_ver,
            "b_ver": b_ver,
            "a_digest": a_digest,
            "b_digest": b_digest,
        }
        return sign_payload(payload, self.key)

    # -- claim / open --------------------------------------------------------

    def open_claimed(self, claim: Dict[str, Any]) -> Dict[str, Any]:
        """Open a claim released by the vault and return the signed receipt."""
        if claim["recipient"] != self.party_id:
            raise ValueError("claim packet is addressed to another party")
        packet_key = decrypt(claim["envelope"], self.key)
        plaintext = seal.unseal(bytes.fromhex(claim["ciphertext"]), packet_key)
        digest = sha256_hex(plaintext)
        if digest != claim["digest"]:
            raise ValueError("opened plaintext does not match the committed digest")
        receipt_payload = {
            "order_id": claim["order_id"],
            "party": self.party_id,
            "token_id": claim["token_id"],
            "read_ver": claim["read_ver"],
            "read_digest": digest,
            "plaintext_digest": digest,
        }
        return {
            "receipt": sign_payload(receipt_payload, self.key),
            "plaintext": plaintext,
        }

    def sign_receipt(self, order_id: str, token_id: str, read_ver: int,
                     read_digest: str, plaintext: bytes) -> Dict[str, Any]:
        """Build a receipt directly (used by tests to forge a stale-token read)."""
        payload = {
            "order_id": order_id,
            "party": self.party_id,
            "token_id": token_id,
            "read_ver": read_ver,
            "read_digest": read_digest,
            "plaintext_digest": sha256_hex(plaintext),
        }
        return sign_payload(payload, self.key)
