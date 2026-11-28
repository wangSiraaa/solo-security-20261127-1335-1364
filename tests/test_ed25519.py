"""RFC 8032 Ed25519 correctness and basic misuse resistance."""
import unittest

from puzzle import ed25519
from puzzle.signing import Key, SignatureError, verify_any


class TestRFC8032Vectors(unittest.TestCase):
    VECTORS = [
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
         "", "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
         "72", "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ]

    def test_public_and_signature_match(self):
        for seed_hex, pub_hex, msg_hex, sig_hex in self.VECTORS:
            seed = bytes.fromhex(seed_hex)
            msg = bytes.fromhex(msg_hex)
            self.assertEqual(ed25519.public_key(seed).hex(), pub_hex)
            self.assertEqual(ed25519.sign(seed, msg).hex(), sig_hex)
            self.assertTrue(ed25519.verify(bytes.fromhex(pub_hex), msg,
                                           bytes.fromhex(sig_hex)))

    def test_tampered_signature_rejected(self):
        _s, pub_hex, msg_hex, sig_hex = self.VECTORS[0]
        sig = bytearray(bytes.fromhex(sig_hex))
        sig[0] ^= 1
        self.assertFalse(ed25519.verify(bytes.fromhex(pub_hex),
                                        bytes.fromhex(msg_hex), bytes(sig)))


class TestEnvelope(unittest.TestCase):
    def test_roundtrip_and_wrong_signer(self):
        k = Key.from_label("a")
        other = Key.from_label("b")
        env = k.sign_body({"hello": 1, "signer_expected": True})
        self.assertEqual(verify_any(env)["hello"], 1)
        with self.assertRaises(SignatureError):
            other.verify_envelope(env)

    def test_modified_body_rejected(self):
        k = Key.from_label("a")
        env = k.sign_body({"x": 1})
        env["body"]["x"] = 2
        with self.assertRaises(SignatureError):
            verify_any(env)

    def test_deterministic_string_keys(self):
        self.assertEqual(Key.from_label("z").pub_hex,
                         Key.from_label("z").pub_hex)


if __name__ == "__main__":
    unittest.main()
