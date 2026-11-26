"""Authenticated, event-scoped, member-blind participation slips (凭条).

A slip is a compact string::

    v1:<event>:<token_id>:<nonce>:<mac>

``mac`` = HMAC-SHA256 over the rest, keyed by the event's secret.  Consequences
we rely on elsewhere:

* **Event scoping** -- the MAC covers the event id, so a slip minted for the
  *previous* event fails verification at the current event's hall (the hall
  holds only the current event key).  This is the "凭条的活动改成上一场，应在
  投递前发现证不符" requirement.
* **Forgery resistance** -- an attacker who cannot compute the MAC cannot mint
  slips.  The token id and nonce are 128-bit random values.
* **Member blindness** -- nothing on the slip identifies the member; the desk
  keeps the member<->token mapping separately.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .model import Event, InvalidSignature, Token

_PREFIX = "v1"
_NOISE = b"ballotbox/slip/v1"


def _mac(key: bytes, event_id: str, token_id: str, nonce: str) -> str:
    msg = "\x1f".join((_PREFIX, event_id, token_id, nonce)).encode("utf-8")
    return hmac.new(key, _NOISE + msg, hashlib.sha256).hexdigest()


def issue_token(event: Event) -> Token:
    """Mint a fresh slip for ``event``.  Random, unforgeable, member-free."""
    token_id = secrets.token_hex(16)
    nonce = secrets.token_hex(8)
    mac = _mac(event.key, event.event_id, token_id, nonce)
    slip = f"{_PREFIX}:{event.event_id}:{token_id}:{nonce}:{mac}"
    return Token(token_id=token_id, event_id=event.event_id, slip=slip)


def parse_slip(slip: str):
    parts = slip.split(":")
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise InvalidSignature("malformed slip")
    _, event_id, token_id, nonce, mac = parts
    return event_id, token_id, nonce, mac


def verify_slip(slip: str, event: Event) -> Token:
    """Verify a slip *against a specific event*.

    Raises ``InvalidSignature`` for a forged/tampered slip and
    ``EventMismatch`` (a subclass of ``InvalidSignature``) when the slip is
    authentic but belongs to a different event.  In both cases the caller
    rejects **before** recording anything.
    """
    from .model import EventMismatch  # local import to keep module deps flat

    event_id, token_id, nonce, mac = parse_slip(slip)
    # Check the declared event *first*: a slip minted for another event is a
    # "证不符" and must be refused before delivery, distinctly from forgery.
    if event_id != event.event_id:
        raise EventMismatch(
            f"slip is for {event_id!r}, hall collects {event.event_id!r}"
        )
    expected = _mac(event.key, event_id, token_id, nonce)
    if not hmac.compare_digest(expected, mac):
        raise InvalidSignature("bad mac")
    return Token(token_id=token_id, event_id=event_id, slip=slip)


__all__ = ["issue_token", "verify_slip", "parse_slip"]
