"""stdlib-only crypto core for the drill.

Primitives (modelled, not production crypto):
  * One DH group: RFC 3526 2048-bit MODP (safe-prime, order Q=(P-1)/2).
    Every party has one keypair (x, y=g^x).  The same public key is the
    verifier key for Schnorr signatures and the peer key for DH.
  * DH -> HKDF(sha256) -> SHA256 counter-mode stream cipher + HMAC, giving a
    *sealed box*: only the addressee's secret key opens the packet.
  * Schnorr signatures (challenge = H(R || msg || y || domain)) for
    vouchers, receipts and public slips.
  * A Mersenne prime field MOD = 2**127 - 1 for the *score arithmetic*
    (additive shares and masks live here).  Kept separate from the group.

Reusing one keypair for DH and Schnorr is fine for a protocol-shape model; a
real deployment would derive independent keys per purpose.
"""

import hashlib
import hmac
import secrets

# --- RFC 3526 2048-bit MODP group (group 14) --------------------------------
P_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
P = int(P_HEX, 16)
G = 2
Q = (P - 1) // 2

MOD = (1 << 127) - 1  # Mersenne prime for score field arithmetic
BYTES16 = 16          # MOD < 2**127, so field elements fit in 16 bytes


def _b(x: int) -> bytes:
    return b"" if x == 0 else x.to_bytes((x.bit_length() + 7) // 8, "big")


def h(*parts: bytes) -> bytes:
    return hashlib.sha256(b"|".join(parts)).digest()


def i2b16(x: int) -> bytes:
    return x.to_bytes(BYTES16, "big")


def b2i(bs: bytes) -> int:
    return int.from_bytes(bs, "big")


# --- party keypair ---------------------------------------------------------

class Party:
    """A keyed party: secret x, public y=g^x."""

    def __init__(self, name: str):
        self.name = name
        self.x = secrets.randbelow(Q - 1) + 1
        self.y = pow(G, self.x, P)

    @property
    def pub(self) -> int:
        return self.y


# --- HKDF / stream cipher / MAC -------------------------------------------

def hkdf_sha256(secret: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + i.to_bytes(4, "big"), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _keystream_xor(key: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 32):
        blk = hashlib.sha256(key + i.to_bytes(8, "big")).digest()
        chunk = data[i:i + 32]
        out += bytes(a ^ b for a, b in zip(chunk, blk))
    return bytes(out)


def dh_shared(secret_x: int, peer_y: int) -> bytes:
    """Diffie-Hellman shared secret bytes (256 bytes)."""
    shared = pow(peer_y, secret_x, P)
    return shared.to_bytes(256, "big")


# --- sealed box -----------------------------------------------------------

def seal(sender: Party, recipient_pub: int, plaintext: bytes, domain: bytes) -> dict:
    """Seal plaintext to ``recipient_pub`` using an ephemeral sender key.

    Returns envelope E containing the ephemeral public value; only the holder
    of the recipient secret can open it.  Not authenticated to the sender; call
    ``sign_envelope`` if the sender identity must be bound.
    """
    eph_x = secrets.randbelow(Q - 1) + 1
    eph_y = pow(G, eph_x, P)
    shared = dh_shared(eph_x, recipient_pub)
    keys = hkdf_sha256(shared, b"seal-salt", domain, 64)
    enc_key, mac_key = keys[:32], keys[32:]
    ct = _keystream_xor(enc_key, plaintext)
    tag = hmac.new(mac_key, ct, hashlib.sha256).digest()
    return {"E": eph_y, "ct": ct, "tag": tag, "domain": domain}


def open_seal(recipient: Party, env: dict) -> bytes:
    shared = dh_shared(recipient.x, env["E"])
    keys = hkdf_sha256(shared, b"seal-salt", env["domain"], 64)
    enc_key, mac_key = keys[:32], keys[32:]
    tag = hmac.new(mac_key, env["ct"], hashlib.sha256).digest()
    if not hmac.compare_digest(tag, env["tag"]):
        raise ValueError("seal authentication failed (wrong key / tampered)")
    return _keystream_xor(enc_key, env["ct"])


def env_digest(env: dict) -> bytes:
    return h(b"env", _b(env["E"]), env["ct"], env["tag"], env["domain"])


# --- Schnorr signatures ----------------------------------------------------

def schnorr_sign(party: Party, msg: bytes, domain: bytes) -> dict:
    k = secrets.randbelow(Q - 1) + 1
    R = pow(G, k, P)
    c = b2i(h(domain, _b(R), _b(party.y), msg)) % Q
    s = (k + c * party.x) % Q
    return {"y": party.y, "R": R, "s": s, "msg": msg, "domain": domain}


def schnorr_verify(sig: dict, expected_y: int | None = None) -> bool:
    """Verify; if expected_y is given the signer's key is pinned.

    Without pinning, an attacker can mint a fresh keypair and sign arbitrary
    messages -- the voucher-forgery path the drill explicitly guards against.
    """
    domain, y, R, s, msg = sig["domain"], sig["y"], sig["R"], sig["s"], sig["msg"]
    if expected_y is not None and y != expected_y:
        return False
    c = b2i(h(domain, _b(R), _b(y), msg)) % Q
    return pow(G, s, P) == (R * pow(y, c, P)) % P


# --- field helpers ---------------------------------------------------------

def fadd(a: int, b: int) -> int:
    return (a + b) % MOD


def fsub(a: int, b: int) -> int:
    return (a - b) % MOD


def fsum(xs) -> int:
    return sum(xs) % MOD


def rand_field() -> int:
    return secrets.randbelow(MOD)


def prf_field(shared: bytes, *labels: bytes) -> int:
    """Deterministic field-valued PRF from a DH secret and labels."""
    info = b"|".join(labels)
    out = hkdf_sha256(shared, b"prf-salt", info, 32)
    return b2i(out) % MOD


def rand_token(n: int = 6) -> str:
    return secrets.token_hex(n)[:n]


def short_hash(*parts: bytes) -> str:
    """Stable short hex digest for human-readable record labels."""
    return hashlib.sha256(b"|".join(parts)).hexdigest()[:12]
