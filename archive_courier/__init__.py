"""Offline compression / packing / receiving tool for archive transmittals.

Design goal
-----------
A carrier (外包承运商) may see, for every transmittal: the number of boxes,
the box sequence, and each box's byte size.  What it must *not* be able to
infer is which *option* (public template vs. a secret one) inside each
short page was chosen.  Several legal options of the same work order must
produce *identical external records* — same box shapes, same byte counts,
same manifest.

Two papers are implemented (paths chosen per the brief):

layout   reserve box shapes for *all* legal versions of the whole order, fill
         every version into the same shapes with padding that decrypts to
         junk, and (crucially) size boxes so a single legal version is not
         split across boxes.  Byte counts are version-independent -> a
         version cannot be told apart even by which pages fall in which box.

shard    page-independent delivery: every page is compressed to a constant
         length determined only by its *public* structure (indices, nothing
         from the secret option).  Constant length means the usual cheap
         short-codes are traded away; the payload is a short code book of
         actual words.  Pages can leave the archive independently.

Wherever two options would compress to different lengths, the *reserved*
shapes are not shrunk: an option never gets an extra box to carry its own
surplus.  Box sizes are driven by the public structure, so both options
fit, with slack.
"""

from .util import ACError

__all__ = ["ACError"]
