"""Gateway/cabinet operations and the slot/order state machine."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .channels import load_verified_witness
from .clockutil import iso, now
from .errors import (
    ChannelBindingError,
    ConflictError,
    FullError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from .security import sha256_hex, sign_token, verify_token, write_private_json
from .store import Store, write_json_atomic

MAX_SEGMENTS = 32
ACTIVE_ORDER_STATES = {"reserved", "writing", "complete"}
WRITABLE_ORDER_STATES = {"reserved", "writing", "complete"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _new_rid(cabinet: Dict[str, Any]) -> str:
    while True:
        rid = "RSV-" + secrets.token_hex(4).upper()
        if rid not in cabinet["orders"]:
            return rid


def _make_ticket(key: bytes, rid: str, created_at: int) -> str:
    # Deterministic for an order: an idempotent first-segment retry in onwrite
    # mode can be handed the same capability again.  The HMAC prevents forgery.
    return sign_token({"typ": "ticket", "rid": rid, "iat": created_at}, key)


def _ticket_rid(key: bytes, ticket: str) -> str:
    return verify_token(ticket, key, expected_type="ticket")["rid"]


def _require_order(cabinet: Dict[str, Any], rid: str) -> Dict[str, Any]:
    order = cabinet["orders"].get(rid)
    if order is None:
        raise NotFoundError("no such upload order", rid=rid)
    return order


def _check_ticket(key: bytes, cabinet: Dict[str, Any], ticket: str) -> Dict[str, Any]:
    payload = verify_token(ticket, key, expected_type="ticket")
    order = _require_order(cabinet, payload["rid"])
    if order.get("ticket_hash") != sha256_hex(ticket):
        raise UnauthorizedError("ticket does not match this order", rid=order["rid"])
    return order


def _append_event(order: Dict[str, Any], state: str, message: str, ts: int) -> None:
    order.setdefault("events", []).append(
        {"at": ts, "time": iso(ts), "state": state, "message": message}
    )


def _note_egress(order: Dict[str, Any], egress: str | None, operation: str, ts: int) -> None:
    if not egress:
        return
    order.setdefault("egress_observations", []).append(
        {
            "at": ts,
            "time": iso(ts),
            "operation": operation,
            "egress": egress,
            "authorization": "egress_name_is_not_identity",
        }
    )


def _release_slot(cabinet: Dict[str, Any], order: Dict[str, Any], ts: int) -> None:
    slot_index = order.get("slot")
    if slot_index is None:
        return
    slot = cabinet["slots"][int(slot_index) - 1]
    if slot.get("order_id") == order["rid"]:
        slot["order_id"] = None
        slot["state"] = "empty"
        slot["last_order_id"] = order["rid"]
    order["slot_released_at"] = ts
    order["slot_released_time"] = iso(ts)


def reap_expired(cabinet: Dict[str, Any], ts: int) -> List[Dict[str, Any]]:
    """Release only holds whose explicit lifetime has ended."""

    reaped: List[Dict[str, Any]] = []
    for order in cabinet["orders"].values():
        rid = order["rid"]
        if order["state"] == "reserved":
            deadline = int(order.get("reservation_expires_at", 0))
            if deadline and ts >= deadline:
                order["state"] = "expired"
                order["reaped_at"] = ts
                order["reaped_time"] = iso(ts)
                _append_event(order, "expired", "time-limited reservation expired", ts)
                _release_slot(cabinet, order, ts)
                reaped.append({"rid": rid, "from_state": "reserved", "slot": order.get("slot")})
        elif order["state"] in {"writing", "complete"}:
            deadline = int(order.get("partial_expires_at") or 0)
            if deadline and ts >= deadline:
                previous = order["state"]
                order["state"] = "abandoned"
                order["reaped_at"] = ts
                order["reaped_time"] = iso(ts)
                _append_event(order, "abandoned", f"{previous} upload exceeded its deadline", ts)
                _release_slot(cabinet, order, ts)
                reaped.append({"rid": rid, "from_state": previous, "slot": order.get("slot")})
    return reaped


def reap_now(store: Store) -> Dict[str, Any]:
    ts = now()
    reaped = store.update(lambda cabinet: reap_expired(cabinet, ts))
    return {"ok": True, "reaped": reaped, "count": len(reaped), "now": ts, "now_iso": iso(ts)}


def _free_slot(cabinet: Dict[str, Any]) -> Dict[str, Any] | None:
    """Return an assignable slot.

    Delivered orders release the *hold* after final sign-off, but remain visible
    in their cell until that cell is inspected/recycled or explicitly cleared.
    Active reservations and half-written items are never returned here.
    """

    for slot in cabinet["slots"]:
        rid = slot.get("order_id")
        if not rid:
            return slot
        order = cabinet["orders"].get(rid)
        if (
            order is not None
            and order.get("state") == "delivered"
            and order.get("slot_released_at")
        ):
            return slot
    return None


def _claim_free_slot(cabinet: Dict[str, Any], ts: int) -> Dict[str, Any] | None:
    slot = _free_slot(cabinet)
    if slot is None:
        return None
    previous_rid = slot.get("order_id")
    if previous_rid:
        previous = cabinet["orders"].get(previous_rid)
        if previous is not None:
            previous["slot_recycled_at"] = ts
            _append_event(
                previous,
                "delivered",
                "released cell recycled for a new accepted order",
                ts,
            )
        slot["last_order_id"] = previous_rid
    return slot


def _missing_segments(order: Dict[str, Any]) -> List[int]:
    received = {int(index) for index in order.get("segments", {})}
    return [index for index in range(int(order["segments_total"])) if index not in received]


def _slot_view(slot: Dict[str, Any], cabinet: Dict[str, Any]) -> Dict[str, Any]:
    index = slot["index"]
    rid = slot.get("order_id")
    if not rid:
        return {
            "slot": index,
            "state": "empty",
            "label": "空格",
            "order_id": None,
            "releasable": False,
        }
    order = cabinet["orders"][rid]
    missing = _missing_segments(order)
    state = order["state"]
    labels = {
        "reserved": "占位(尚无正文)",
        "writing": f"已写段 {order['segments_total'] - len(missing)}/{order['segments_total']}",
        "complete": "片段齐全(待最终签收)",
        "delivered": "已交付成件",
        "expired": "预约已过期",
        "abandoned": "半件已逾期",
    }
    return {
        "slot": index,
        "state": state,
        "label": labels.get(state, state),
        "order_id": rid,
        "segments_total": order["segments_total"],
        "segments_received": len(order.get("segments", {})),
        "received_indexes": sorted(int(k) for k in order.get("segments", {})),
        "missing_indexes": missing,
        "bound_channel": order.get("bound_channel"),
        "created_at": order.get("created_at"),
        "reservation_expires_at": order.get("reservation_expires_at"),
        "partial_expires_at": order.get("partial_expires_at"),
        "delivered_at": order.get("delivered_at"),
        "signoff": (order.get("signoff") or {}).get("text"),
        "artifact_path": order.get("artifact_path"),
        "package_sha256": order.get("package_sha256"),
        "hold_released": state == "delivered",
        "reusable_for_new_order": state == "delivered",
        "releasable_by_clear_command": state in {"delivered", "expired", "abandoned"},
    }


def inspect_cabinet(store: Store, slot_filter: int | None = None) -> Dict[str, Any]:
    config = store.config()
    cabinet = store.read_cabinet()
    ts = now()
    slots = [_slot_view(slot, cabinet) for slot in cabinet["slots"]]
    if slot_filter is not None:
        if not 1 <= slot_filter <= len(slots):
            raise NotFoundError("no such slot", slot=slot_filter, slots=len(slots))
        slots = [slots[slot_filter - 1]]
    counts: Dict[str, int] = {}
    for view in (_slot_view(slot, cabinet) for slot in cabinet["slots"]):
        counts[view["state"]] = counts.get(view["state"], 0) + 1
    return {
        "ok": True,
        "now": ts,
        "now_iso": iso(ts),
        "state_directory": str(store.root),
        "policy": config["policy"],
        "slots_count": int(config["slots"]),
        "counts": counts,
        "slots": slots,
        "orders": list(cabinet["orders"].values()),
    }


# ---------------------------------------------------------------------------
# First question
# ---------------------------------------------------------------------------

def ask(
    store: Store,
    witness_path: str | Path,
    segments_total: int,
    egress: str | None = None,
    ticket_out: str | Path | None = None,
    voucher_out: str | Path | None = None,
    upload_key: str | None = None,
) -> Dict[str, Any]:
    if not 1 <= int(segments_total) <= MAX_SEGMENTS:
        raise ValidationError("segments must be between 1 and 32", segments=segments_total)
    witness = load_verified_witness(store, witness_path)
    config = store.config()
    policy = config["policy"]
    ts = now()

    # Global TTL maintenance is allowed; it is not per-applicant state.
    store.update(lambda cabinet: reap_expired(cabinet, ts))

    if policy == "onwrite":
        upload_key = upload_key or "uk-" + secrets.token_hex(12)
        key = store.key()
        voucher = sign_token(
            {
                "typ": "try_voucher",
                "channel": witness["channel"],
                "segments_total": int(segments_total),
                "upload_key_hash": sha256_hex(upload_key),
                "iat": ts,
                "exp": ts + int(config["witness_ttl_seconds"]),
            },
            key,
        )
        cabinet = store.read_cabinet()
        free_now = sum(1 for slot in cabinet["slots"] if not slot.get("order_id"))
        result = {
            "ok": True,
            "status": "try_voucher",
            "policy": policy,
            "seat_held": False,
            "promise": "none",
            "retry_allowed": True,
            "warning": "This voucher only permits a first-segment attempt; a slot is assigned atomically with that segment.",
            "voucher": voucher,
            "upload_key": upload_key,
            "bound_channel": witness["channel"],
            "segments_total": int(segments_total),
            "free_slots_at_question_time": free_now,
        }
        if voucher_out:
            path = write_private_json(
                voucher_out,
                {
                    "schema": "anon-station-voucher/1",
                    "voucher": voucher,
                    "upload_key": upload_key,
                    "bound_channel": witness["channel"],
                    "segments_total": int(segments_total),
                    "note": "private; this is not a slot reservation",
                },
            )
            result["voucher_path"] = str(path)
        return result

    # reservation policy: a successful response is a durable promise.
    key = store.key()
    saved: Dict[str, Any] = {}

    def mutate(cabinet: Dict[str, Any]) -> None:
        reap_expired(cabinet, ts)
        slot = _claim_free_slot(cabinet, ts)
        if slot is None:
            views = [_slot_view(item, cabinet) for item in cabinet["slots"]]
            raise FullError(
                "no free staging slot for a durable reservation",
                seat_held=False,
                promise="none",
                retry_allowed=True,
                occupied_slots=views,
            )
        rid = _new_rid(cabinet)
        ticket = _make_ticket(key, rid, ts)
        order = {
            "rid": rid,
            "policy": "reservation",
            "state": "reserved",
            "slot": slot["index"],
            "segments_total": int(segments_total),
            "segments": {},
            "idempotency_keys": {},
            "ticket_hash": sha256_hex(ticket),
            "upload_key_hash": None,
            "bound_channel": witness["channel"],
            "channel_history": [
                {"at": ts, "time": iso(ts), "event": "reserved", "channel": witness["channel"]}
            ],
            "egress_observations": [],
            "created_at": ts,
            "created_iso": iso(ts),
            "reservation_expires_at": ts + int(config["reservation_ttl_seconds"]),
            "reservation_expires_iso": iso(ts + int(config["reservation_ttl_seconds"])),
            "partial_expires_at": None,
            "package_sha256": None,
            "signoff": None,
            "delivered_at": None,
            "artifact_path": None,
            "events": [],
        }
        _append_event(order, "reserved", "durable time-limited slot reservation", ts)
        _note_egress(order, egress, "ask", ts)
        cabinet["orders"][rid] = order
        slot["order_id"] = rid
        slot["state"] = "reserved"
        saved.update(
            {
                "rid": rid,
                "slot": slot["index"],
                "ticket": ticket,
                "reservation_expires_at": order["reservation_expires_at"],
                "reservation_expires_iso": order["reservation_expires_iso"],
            }
        )

    store.update(mutate)
    result = {
        "ok": True,
        "status": "reserved",
        "policy": policy,
        "seat_held": True,
        "promise": "slot_held_until_reservation_ttl",
        "retry_allowed": True,
        "rid": saved["rid"],
        "slot": saved["slot"],
        "ticket": saved["ticket"],
        "bound_channel": witness["channel"],
        "segments_total": int(segments_total),
        "reservation_expires_at": saved["reservation_expires_at"],
        "reservation_expires_iso": saved["reservation_expires_iso"],
    }
    if ticket_out:
        path = write_private_json(
            ticket_out,
            {
                "schema": "anon-station-ticket/1",
                "rid": saved["rid"],
                "slot": saved["slot"],
                "ticket": saved["ticket"],
                "note": "private claim stub; authorization follows this ticket, not the wireless line or egress name",
            },
        )
        result["ticket_path"] = str(path)
    return result


# ---------------------------------------------------------------------------
# Line migration
# ---------------------------------------------------------------------------

def migrate_line(
    store: Store,
    ticket: str,
    witness_path: str | Path,
    egress: str | None = None,
) -> Dict[str, Any]:
    witness = load_verified_witness(store, witness_path)
    key = store.key()
    rid = _ticket_rid(key, ticket)
    ts = now()
    output: Dict[str, Any] = {}

    def mutate(cabinet: Dict[str, Any]) -> None:
        order = _check_ticket(key, cabinet, ticket)
        if order["rid"] != rid:  # defensive; signed token already fixes rid
            raise UnauthorizedError("ticket rid mismatch")
        if order["state"] not in ACTIVE_ORDER_STATES:
            raise ConflictError(
                "cannot migrate a closed order",
                rid=rid,
                order_state=order["state"],
            )
        old_channel = order.get("bound_channel")
        new_channel = witness["channel"]
        if old_channel != new_channel:
            order["channel_history"].append(
                {
                    "at": ts,
                    "time": iso(ts),
                    "event": "migrated",
                    "from_channel": old_channel,
                    "to_channel": new_channel,
                    "authorization": "private_ticket_plus_current_signed_witness",
                }
            )
            order["bound_channel"] = new_channel
            _append_event(order, order["state"], f"channel migrated {old_channel} -> {new_channel}", ts)
        _note_egress(order, egress, "migrate", ts)
        output.update(
            {
                "rid": rid,
                "old_channel": old_channel,
                "bound_channel": new_channel,
                "changed": old_channel != new_channel,
            }
        )

    store.update(mutate)
    return {"ok": True, "status": "migrated", **output}


# ---------------------------------------------------------------------------
# Segment writes
# ---------------------------------------------------------------------------

def _check_deadlines(order: Dict[str, Any], config: Dict[str, Any], ts: int) -> None:
    if order["state"] == "reserved":
        deadline = int(order.get("reservation_expires_at") or 0)
        if deadline and ts >= deadline:
            order["state"] = "expired"
            order["reaped_at"] = ts
            _append_event(order, "expired", "reservation expired before first segment", ts)
            raise ConflictError("reservation has expired", code_override="RESERVATION_EXPIRED", rid=order["rid"])
    if order["state"] in {"writing", "complete"}:
        deadline = int(order.get("partial_expires_at") or 0)
        if deadline and ts >= deadline:
            order["state"] = "abandoned"
            order["reaped_at"] = ts
            _append_event(order, "abandoned", "half upload exceeded its deadline", ts)
            raise ConflictError("upload has exceeded its deadline", code_override="UPLOAD_EXPIRED", rid=order["rid"])


def _apply_segment(
    cabinet: Dict[str, Any],
    config: Dict[str, Any],
    key: bytes,
    order: Dict[str, Any],
    *,
    index: int,
    data: str,
    witness_channel: str,
    egress: str | None,
    idempotency_key: str | None,
    ts: int,
    ticket_to_return: str | None = None,
) -> Dict[str, Any]:
    if order["state"] in {"expired", "abandoned"}:
        raise ConflictError("order is closed", rid=order["rid"], order_state=order["state"])
    if order["state"] == "delivered":
        raise ConflictError("order is already delivered", rid=order["rid"])
    _check_deadlines(order, config, ts)

    if witness_channel != order.get("bound_channel"):
        raise ChannelBindingError(
            "body witness is for a different channel and this reservation has not been migrated",
            rid=order["rid"],
            bound_channel=order.get("bound_channel"),
            witness_channel=witness_channel,
            migration_required=True,
            present_ticket_and_new_witness_command="migrate",
        )

    total = int(order["segments_total"])
    if not 0 <= int(index) < total:
        raise ValidationError("segment index outside declared range", index=index, segments_total=total)

    digest = sha256_hex(data)
    idempotency = order.setdefault("idempotency_keys", {})
    if idempotency_key:
        prior_request = idempotency.get(idempotency_key)
        if prior_request and (
            prior_request.get("index") != int(index)
            or prior_request.get("sha256") != digest
        ):
            raise ConflictError(
                "idempotency key was already used with a different segment or body",
                code_override="IDEMPOTENCY_KEY_REUSED",
                idempotency_key=idempotency_key,
            )

    segment = order["segments"].get(str(index))
    duplicate = False
    if segment is not None:
        if segment.get("sha256") != digest:
            raise ConflictError(
                "same segment index already contains different content; refusing to overwrite",
                code_override="SEGMENT_CONFLICT",
                rid=order["rid"],
                index=index,
                existing_sha256=segment.get("sha256"),
                incoming_sha256=digest,
            )
        duplicate = True
    else:
        order["segments"][str(index)] = {
            "index": int(index),
            "data": data,
            "sha256": digest,
            "received_at": ts,
            "received_iso": iso(ts),
            "idempotency_key": idempotency_key,
        }
        if idempotency_key:
            idempotency[idempotency_key] = {
                "index": int(index),
                "sha256": digest,
                "received_at": ts,
            }
        if order["state"] == "reserved":
            order["state"] = "writing"
            order["partial_expires_at"] = ts + int(config["partial_ttl_seconds"])
            order["partial_expires_iso"] = iso(order["partial_expires_at"])
            _append_event(order, "writing", "first durable segment written", ts)
        if len(order["segments"]) == total:
            order["state"] = "complete"
            order["completed_at"] = ts
            order["completed_iso"] = iso(ts)
            _append_event(order, "complete", "all segments present; awaiting final signoff", ts)

    slot = cabinet["slots"][int(order["slot"]) - 1]
    slot["state"] = order["state"]
    _note_egress(order, egress, "write-duplicate" if duplicate else "write", ts)

    missing = _missing_segments(order)
    result = {
        "ok": True,
        "duplicate": duplicate,
        "rid": order["rid"],
        "slot": order["slot"],
        "index": int(index),
        "state": order["state"],
        "segments_received": len(order["segments"]),
        "segments_total": total,
        "missing_indexes": missing,
        "bound_channel": order.get("bound_channel"),
    }
    if ticket_to_return:
        result["ticket"] = ticket_to_return
    return result


def write_segment(
    store: Store,
    *,
    index: int,
    data: str | None = None,
    data_file: str | Path | None = None,
    witness_path: str | Path,
    ticket: str | None = None,
    voucher: str | None = None,
    upload_key: str | None = None,
    egress: str | None = None,
    idempotency_key: str | None = None,
    ticket_out: str | Path | None = None,
) -> Dict[str, Any]:
    if data is None and data_file is not None:
        data = Path(data_file).read_text(encoding="utf-8")
    if data is None:
        raise ValidationError("either --data or --data-file is required")

    witness = load_verified_witness(store, witness_path)
    config = store.config()
    key = store.key()
    ts = now()
    store.update(lambda cabinet: reap_expired(cabinet, ts))

    provided = sum(bool(x) for x in (ticket, voucher))
    if provided != 1:
        raise ValidationError("provide exactly one of --ticket-file/--ticket or --voucher-file/--voucher")

    raw_ticket_to_save: str | None = None

    def mutate(cabinet: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal raw_ticket_to_save
        if ticket:
            order = _check_ticket(key, cabinet, ticket)
            return _apply_segment(
                cabinet,
                config,
                key,
                order,
                index=index,
                data=data or "",
                witness_channel=witness["channel"],
                egress=egress,
                idempotency_key=idempotency_key,
                ts=ts,
            )

        # onwrite first allocation path.
        voucher_payload = verify_token(voucher, key, expected_type="try_voucher")
        if not upload_key:
            raise ValidationError("--upload-key is required with a try voucher")
        if voucher_payload.get("upload_key_hash") != sha256_hex(upload_key):
            raise UnauthorizedError("upload key does not match signed voucher")
        if int(index) != 0:
            raise ValidationError("the first slot-allocating write must be segment index 0")

        upload_key_hash = sha256_hex(upload_key)
        existing = next(
            (
                order
                for order in cabinet["orders"].values()
                if order.get("upload_key_hash") == upload_key_hash
            ),
            None,
        )
        if existing is not None:
            # Retry of the allocating request. Do not consume another slot.
            retry_ticket = _make_ticket(key, existing["rid"], int(existing["created_at"]))
            raw_ticket_to_save = retry_ticket
            result = _apply_segment(
                cabinet,
                config,
                key,
                existing,
                index=index,
                data=data or "",
                witness_channel=witness["channel"],
                egress=egress,
                idempotency_key=idempotency_key,
                ts=ts,
                ticket_to_return=retry_ticket,
            )
            result["allocated"] = False
            result["idempotent_first_segment"] = True
            return result

        slot = _claim_free_slot(cabinet, ts)
        if slot is None:
            views = [_slot_view(item, cabinet) for item in cabinet["slots"]]
            raise FullError(
                "no slot when first segment arrived; the earlier voucher was only permission to try",
                code_override="FULL_AT_FIRST_SEGMENT",
                seat_held=False,
                promise="none",
                retry_allowed=True,
                occupied_slots=views,
            )

        rid = _new_rid(cabinet)
        new_ticket = _make_ticket(key, rid, ts)
        raw_ticket_to_save = new_ticket
        segments_total = int(voucher_payload["segments_total"])
        order = {
            "rid": rid,
            "policy": "onwrite",
            "state": "reserved",
            "slot": slot["index"],
            "segments_total": segments_total,
            "segments": {},
            "idempotency_keys": {},
            "ticket_hash": sha256_hex(new_ticket),
            "upload_key_hash": upload_key_hash,
            "bound_channel": witness["channel"],
            "channel_history": [
                {"at": ts, "time": iso(ts), "event": "allocated-with-first-segment", "channel": witness["channel"]}
            ],
            "egress_observations": [],
            "created_at": ts,
            "created_iso": iso(ts),
            "reservation_expires_at": None,
            "partial_expires_at": None,
            "package_sha256": None,
            "signoff": None,
            "delivered_at": None,
            "artifact_path": None,
            "events": [],
        }
        cabinet["orders"][rid] = order
        slot["order_id"] = rid
        slot["state"] = "reserved"
        result = _apply_segment(
            cabinet,
            config,
            key,
            order,
            index=0,
            data=data or "",
            witness_channel=witness["channel"],
            egress=egress,
            idempotency_key=idempotency_key,
            ts=ts,
            ticket_to_return=new_ticket,
        )
        result["allocated"] = True
        result["seat_held_after_write"] = True
        return result

    result = store.update(mutate)
    if ticket_out and raw_ticket_to_save:
        order_result = result
        path = write_private_json(
            ticket_out,
            {
                "schema": "anon-station-ticket/1",
                "rid": order_result["rid"],
                "slot": order_result["slot"],
                "ticket": raw_ticket_to_save,
                "note": "private claim stub for remaining segments",
            },
        )
        result["ticket_path"] = str(path)
    return result


# ---------------------------------------------------------------------------
# Final sign-off and delivery
# ---------------------------------------------------------------------------

def deliver_item(
    store: Store,
    ticket: str,
    signoff: str,
    out: str | Path | None = None,
) -> Dict[str, Any]:
    if not signoff or not signoff.strip():
        raise ValidationError("a final sign-off text/name is required via --signoff")
    key = store.key()
    rid = _ticket_rid(key, ticket)
    ts = now()
    config = store.config()
    store.update(lambda cabinet: reap_expired(cabinet, ts))
    saved_artifact: Dict[str, Any] = {}

    def mutate(cabinet: Dict[str, Any]) -> Dict[str, Any]:
        order = _check_ticket(key, cabinet, ticket)
        if order["state"] == "delivered":
            saved_artifact["path"] = order.get("artifact_path")
            saved_artifact["payload"] = order.get("delivered_artifact")
            return {
                "ok": True,
                "duplicate_delivery": True,
                "rid": rid,
                "slot": order["slot"],
                "artifact_path": order.get("artifact_path"),
                "artifact": order.get("delivered_artifact"),
            }
        if order["state"] not in ACTIVE_ORDER_STATES:
            raise ConflictError(
                "order is closed and cannot be delivered",
                code_override="ORDER_NOT_DELIVERABLE",
                rid=rid,
                state=order["state"],
            )
        _check_deadlines(order, config, ts)
        missing = _missing_segments(order)
        if missing or order["state"] not in {"complete", "writing"}:
            raise ConflictError(
                "cannot deliver until every segment is present",
                code_override="INCOMPLETE_PACKAGE",
                rid=rid,
                missing_indexes=missing,
                state=order["state"],
            )
        if order["state"] != "complete":
            # Defensive: missing list should normally catch this.
            raise ConflictError("package is not complete", rid=rid, state=order["state"])

        segments: List[str] = [
            order["segments"][str(index)]["data"]
            for index in range(int(order["segments_total"]))
        ]
        assembled = "".join(segments)
        digest = sha256_hex(assembled)
        artifact = {
            "schema": "anon-station-delivery/1",
            "rid": rid,
            "slot": order["slot"],
            "delivered_at": ts,
            "delivered_iso": iso(ts),
            "segments": segments,
            "assembled": assembled,
            "sha256": digest,
            "signoff": {"text": signoff.strip(), "at": ts, "time": iso(ts)},
        }
        json_path = store.deliveries_dir / f"{rid}.json"
        text_path = store.deliveries_dir / f"{rid}.txt"
        write_json_atomic(json_path, artifact)
        text_path.write_text(assembled + "\n", encoding="utf-8")
        text_path.chmod(0o600)

        order["state"] = "delivered"
        order["delivered_at"] = ts
        order["delivered_iso"] = iso(ts)
        order["slot_released_at"] = ts
        order["slot_released_iso"] = iso(ts)
        order["package_sha256"] = digest
        order["signoff"] = artifact["signoff"]
        order["artifact_path"] = str(json_path)
        order["text_artifact_path"] = str(text_path)
        order["delivered_artifact"] = artifact
        _append_event(order, "delivered", f"final sign-off: {signoff.strip()}", ts)
        slot = cabinet["slots"][int(order["slot"]) - 1]
        slot["state"] = "delivered"
        slot["hold_released_at"] = ts
        saved_artifact["path"] = str(json_path)
        saved_artifact["payload"] = artifact
        return {
            "ok": True,
            "duplicate_delivery": False,
            "rid": rid,
            "slot": order["slot"],
            "state": "delivered",
            "artifact_path": str(json_path),
            "text_artifact_path": str(text_path),
            "artifact": artifact,
        }

    result = store.update(mutate)
    if out:
        output_path = Path(out)
        write_private_json(output_path, saved_artifact.get("payload") or result.get("artifact"))
        result["copied_to"] = str(output_path)
    return result


def clear_slot(store: Store, slot_index: int, operator: str) -> Dict[str, Any]:
    ts = now()

    def mutate(cabinet: Dict[str, Any]) -> Dict[str, Any]:
        if not 1 <= slot_index <= len(cabinet["slots"]):
            raise NotFoundError("no such slot", slot=slot_index)
        slot = cabinet["slots"][slot_index - 1]
        rid = slot.get("order_id")
        if not rid:
            return {"ok": True, "already_empty": True, "slot": slot_index}
        order = _require_order(cabinet, rid)
        if order["state"] not in {"delivered", "expired", "abandoned"}:
            raise ConflictError(
                "slot cannot be released while it holds a reservation or written half-item",
                code_override="SLOT_NOT_RELEASABLE",
                slot=slot_index,
                rid=rid,
                order_state=order["state"],
                missing_indexes=_missing_segments(order),
            )
        _release_slot(cabinet, order, ts)
        slot["cleared_at"] = ts
        slot["cleared_by"] = operator or "operator"
        return {
            "ok": True,
            "already_empty": False,
            "slot": slot_index,
            "cleared_order": rid,
            "order_state": order["state"],
            "operator": operator,
        }

    return store.update(mutate)
