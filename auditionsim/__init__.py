"""Local privacy-preserving tally drill (audition points -> joint booth).

All crypto is stdlib-only. Asymmetric primitives (Schnorr signatures for
vouchers/tickets, ElGamal sealed envelopes for share delivery) run in the
RFC 3526 2048-bit MODP group. Score arithmetic uses the Mersenne prime
MOD = 2**127 - 1.
"""

MOD = (1 << 127) - 1  # Mersenne prime field for score arithmetic
