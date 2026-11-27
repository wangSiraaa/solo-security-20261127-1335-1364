"""Offline reading-room email reader.

A small, dependency-free toolkit that

* imports a wire mailbox once ("at arrival") into a sealed local edition,
* lets readers browse that edition completely offline, and
* prints entry guidance from an arrival gate to a lecture venue, honouring
  one-way passages, stair-only shortcuts and wheelchair / step-free routes.

The only network activity ever performed is the issue-level retrieval of
*shared public illustrations* at seal time.  Per-subscriber image addresses
are never requested, so the image supply station cannot learn which reader
opened which letter.
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
