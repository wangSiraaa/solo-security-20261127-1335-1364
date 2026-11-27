"""Tests for the sealed-box primitive (stdlib HMAC-SHA256 construction)."""

import os
import unittest

from synth_ime import seal


class SealTests(unittest.TestCase):
    def test_roundtrip(self):
        key = seal.new_key()
        msg = b"bra ka lo"
        blob = seal.seal(key, msg, b"ctx")
        self.assertEqual(seal.unseal(key, blob, b"ctx"), msg)

    def test_ciphertext_hides_plaintext(self):
        key = seal.new_key()
        blob = seal.seal(key, b"very private sentence", b"")
        self.assertNotIn(b"private", blob)

    def test_wrong_key_fails(self):
        blob = seal.seal(seal.new_key(), b"x", b"")
        with self.assertRaises(seal.SealError):
            seal.unseal(seal.new_key(), blob, b"")

    def test_wrong_aad_fails(self):
        key = seal.new_key()
        blob = seal.seal(key, b"x", b"member=1")
        with self.assertRaises(seal.SealError):
            seal.unseal(key, blob, b"member=2")

    def test_tampered_ciphertext_fails(self):
        key = seal.new_key()
        blob = bytearray(seal.seal(key, b"hello world", b""))
        blob[seal.NONCE_SIZE] ^= 0x01
        with self.assertRaises(seal.SealError):
            seal.unseal(key, bytes(blob), b"")

    def test_nonce_is_random(self):
        key = seal.new_key()
        a = seal.seal(key, b"same", b"")
        b = seal.seal(key, b"same", b"")
        self.assertNotEqual(a, b)

    def test_empty_plaintext(self):
        key = seal.new_key()
        blob = seal.seal(key, b"", b"aad")
        self.assertEqual(seal.unseal(key, blob, b"aad"), b"")

    def test_token_commitment_is_one_way(self):
        tok = seal.new_token()
        h = seal.token_hash(tok)
        self.assertEqual(h, seal.token_hash(tok))
        self.assertNotIn(tok, h)
        self.assertNotEqual(h, seal.token_hash(tok + "x"))


if __name__ == "__main__":
    unittest.main()
