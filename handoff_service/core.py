"""Peripheral hand-off service for a shared repair bench.

The important invariant is enforced here: an input is written to a named work
order only when its component, interface, capability, path, and historical
fixture/job context are all attested at the time the input arrives.  Everything
else is rejected or placed in the unclaimed area, never silently posted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable
import copy

JSONDict = dict[str, Any]


class HandOffError(Exception):
    """The command is malformed or violates a physical-state precondition."""

    def __init__(self, code: str, message: str, details: JSONDict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ConfirmationRequired(HandOffError):
    def __init__(self, message: str, details: JSONDict):
        super().__init__("confirmation_required", message, details)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_state() -> JSONDict:
    return {
        "schema": 1,
        "created_at": now_iso(),
        "seq": 0,
        "components": {},
        "seals": [],
        "ports": {},
        "frozen_circuits": {},
        "fixtures": {},
        "patterns": {},
        "orders": {},
        "entries": [],
        "quarantine": [],
        "rejected_inputs": [],
        "losses": [],
        "buffered_reads": [],
        "receipts": [],
        "attestations": [],
        "handoffs": {},
        "channel_returns": [],
        "audit": [],
        "bus": {
            "lever": "original",
            "original_connected": True,
            "backup_connected": False,
            "backup_board": None,
            "backup_component": None,
        },
        "counters": {"entry": 0, "quarantine": 0, "loss": 0, "buffer": 0},
    }


def _next_id(state: JSONDict, key: str, prefix: str) -> str:
    state["counters"][key] = state["counters"].get(key, 0) + 1
    return f"{prefix}-{state['counters'][key]}"


def _audit(state: JSONDict, kind: str, details: JSONDict, at: str | None = None) -> JSONDict:
    state["seq"] += 1
    event = {
        "seq": state["seq"],
        "at": at or now_iso(),
        "kind": kind,
        "details": details,
    }
    state["audit"].append(event)
    return event


def _ensure_port(state: JSONDict, interface: str, circuit: str | None = None) -> JSONDict:
    port = state["ports"].setdefault(
        interface,
        {
            "interface": interface,
            "circuit": circuit,
            "installed_component": None,
            "install_seq": None,
            "witness": None,
            "frozen": False,
            "freeze_reason": None,
        },
    )
    if circuit and not port.get("circuit"):
        port["circuit"] = circuit
    return port


def _find_seal(
    state: JSONDict, component: str, interface: str, capability: str
) -> JSONDict | None:
    for seal in state["seals"]:
        if (
            seal.get("status", "sealed") == "sealed"
            and seal["component"] == component
            and seal["interface"] == interface
            and seal["capability"] == capability
        ):
            return seal
    return None


def _public_bus(state: JSONDict) -> JSONDict:
    return copy.deepcopy(state["bus"])


def cmd_reset(state: JSONDict, args: JSONDict) -> JSONDict:
    fresh = new_state()
    if args.get("seed"):
        state.clear()
        state.update(fresh)
        result = seed_demo(state)
        return {"reset": True, "seeded": True, **result}
    state.clear()
    state.update(fresh)
    return {"reset": True}


def cmd_component_register(state: JSONDict, args: JSONDict) -> JSONDict:
    component_id = args["id"]
    component = {
        "id": component_id,
        "kind": args.get("kind", "block"),
        "capabilities": list(args.get("capabilities", [])),
        "entrusted": bool(args.get("entrusted", False)),
        "metadata": args.get("metadata", {}),
    }
    state["components"][component_id] = component
    ev = _audit(state, "component_registered", component, args.get("at"))
    return {"component": component, "seq": ev["seq"]}


def cmd_seal_add(state: JSONDict, args: JSONDict) -> JSONDict:
    seal = {
        "id": args["id"],
        "component": args["component"],
        "interface": args["interface"],
        "capability": args["capability"],
        "circuit": args.get("circuit"),
        "status": "sealed",
        "sealed_at": args.get("at") or now_iso(),
    }
    if any(s["id"] == seal["id"] for s in state["seals"]):
        raise HandOffError("seal_exists", f"seal {seal['id']} already exists")
    state["seals"].append(seal)
    ev = _audit(state, "seal_added", seal, args.get("at"))
    return {"seal": seal, "seq": ev["seq"]}


def cmd_install(state: JSONDict, args: JSONDict) -> JSONDict:
    interface = args["interface"]
    component_id = args["component"]
    port = _ensure_port(state, interface, args.get("circuit"))
    if port.get("installed_component") and port["installed_component"] != component_id:
        raise HandOffError(
            "interface_occupied",
            f"{interface} still has {port['installed_component']}; remove it first",
            {"installed_component": port["installed_component"]},
        )
    port.update(
        {
            "installed_component": component_id,
            "install_seq": state["seq"] + 1,
            "witness": None,
            "trust_status": "installed_unwitnessed",
        }
    )
    # A circuit freeze remains an explicit operator policy until lifted.
    if port.get("circuit") in state.get("frozen_circuits", {}):
        port["frozen"] = True
        port["freeze_reason"] = state["frozen_circuits"][port["circuit"]]
    ev = _audit(
        state,
        "component_installed",
        {"interface": interface, "component": component_id},
        args.get("at"),
    )
    port["install_seq"] = ev["seq"]
    return {"port": copy.deepcopy(port), "seq": ev["seq"]}


def cmd_remove(state: JSONDict, args: JSONDict) -> JSONDict:
    interface = args["interface"]
    port = _ensure_port(state, interface)
    removed = port.get("installed_component")
    if not removed and not args.get("force"):
        raise HandOffError("nothing_installed", f"nothing installed on {interface}")
    port.update(
        {
            "installed_component": None,
            "install_seq": None,
            "witness": None,
            "trust_status": "removed",
            "frozen": True if args.get("freeze", True) else port.get("frozen", False),
            "freeze_reason": args.get("reason", "component removed; rewitness required"),
        }
    )
    ev = _audit(
        state,
        "component_removed",
        {"interface": interface, "removed_component": removed},
        args.get("at"),
    )
    return {"removed_component": removed, "port": copy.deepcopy(port), "seq": ev["seq"]}


def cmd_witness(state: JSONDict, args: JSONDict) -> JSONDict:
    interface = args["interface"]
    capability = args["capability"]
    port = _ensure_port(state, interface, args.get("circuit"))
    installed = port.get("installed_component")
    if not installed:
        raise HandOffError("nothing_installed", f"cannot witness empty interface {interface}")

    reported = args.get("reported_serial", installed)
    seal = _find_seal(state, reported, interface, capability)
    component = state["components"].get(reported, {})
    identity_match = reported == installed and not args.get("identity_mismatch", False)
    entrusted = bool(component.get("entrusted"))
    ok = bool(identity_match and seal and entrusted and not args.get("fail"))

    reason = None
    if not identity_match:
        reason = "reported_identity_differs_from_actual_install"
    elif not entrusted:
        reason = "component_is_not_entrusted"
    elif not seal:
        reason = "no_custodian_seal_for_component_interface_capability"
    elif args.get("fail"):
        reason = args.get("fail_reason", "witness_appliance_rejected_interface")

    witness = {
        "seq": state["seq"] + 1,
        "ok": ok,
        "interface": interface,
        "capability": capability,
        "reported_serial": reported,
        "installed_component": installed,
        "seal_id": seal["id"] if seal else None,
        "reason": reason,
        "at": args.get("at") or now_iso(),
    }
    port["witness"] = witness
    port["trust_status"] = "witnessed" if ok else "unverified"
    if ok:
        witness["seq"] = state["seq"] + 1
        port["frozen"] = False
        port["freeze_reason"] = None
    ev = _audit(state, "interface_witnessed", witness, args.get("at"))
    witness["seq"] = ev["seq"]
    return {"witness": copy.deepcopy(witness), "trusted": ok, "seq": ev["seq"]}


def cmd_freeze(state: JSONDict, args: JSONDict) -> JSONDict:
    reason = args.get("reason", "segment frozen pending identity/witness review")
    affected: list[str] = []
    if args.get("interface"):
        names = [args["interface"]]
    else:
        names = list(args.get("interfaces", []))
    circuit = args.get("circuit")
    if circuit:
        state.setdefault("frozen_circuits", {})[circuit] = reason
        for name, port in state["ports"].items():
            if port.get("circuit") == circuit and (not names or name in names):
                port["frozen"] = True
                port["freeze_reason"] = reason
                affected.append(name)
    for name in names:
        port = _ensure_port(state, name, circuit)
        port["frozen"] = True
        port["freeze_reason"] = reason
        affected.append(name)
    ev = _audit(
        state,
        "segment_frozen",
        {"interfaces": sorted(set(affected)), "circuit": circuit, "reason": reason},
        args.get("at"),
    )
    return {"frozen_interfaces": sorted(set(affected)), "seq": ev["seq"]}


def cmd_unfreeze(state: JSONDict, args: JSONDict) -> JSONDict:
    circuit = args.get("circuit")
    interface = args.get("interface")
    lifted: list[str] = []
    if circuit:
        state.get("frozen_circuits", {}).pop(circuit, None)
        for name, port in state["ports"].items():
            if port.get("circuit") == circuit:
                if not args.get("force") and not (port.get("witness") or {}).get("ok"):
                    raise HandOffError(
                        "rewitness_required",
                        f"{name} does not have a valid witness; use force only for explicit override",
                    )
                port["frozen"] = False
                port["freeze_reason"] = None
                lifted.append(name)
    if interface:
        port = _ensure_port(state, interface)
        if not args.get("force") and not (port.get("witness") or {}).get("ok"):
            raise HandOffError("rewitness_required", f"{interface} is not validly witnessed")
        port["frozen"] = False
        port["freeze_reason"] = None
        lifted.append(interface)
    ev = _audit(state, "segment_unfrozen", {"interfaces": lifted}, args.get("at"))
    return {"unfrozen_interfaces": sorted(set(lifted)), "seq": ev["seq"]}


def cmd_pattern_register(state: JSONDict, args: JSONDict) -> JSONDict:
    pattern = {
        "id": args["id"],
        "rereadable": bool(args.get("rereadable", False)),
        "kind": args.get("kind", "paper_pattern"),
        "left_fixture": False,
        "metadata": args.get("metadata", {}),
    }
    state["patterns"][pattern["id"]] = pattern
    ev = _audit(state, "pattern_registered", pattern, args.get("at"))
    return {"pattern": pattern, "seq": ev["seq"]}


def cmd_fixture_load(state: JSONDict, args: JSONDict) -> JSONDict:
    fixture_id = args["fixture"]
    pattern_id = args["pattern"]
    if pattern_id not in state["patterns"]:
        raise HandOffError("unknown_pattern", f"register pattern {pattern_id} first")
    state["fixtures"][fixture_id] = {
        "id": fixture_id,
        "pattern_id": pattern_id,
        "locked": bool(args.get("locked", False)),
        "loaded_seq": state["seq"] + 1,
    }
    ev = _audit(
        state,
        "fixture_loaded",
        {"fixture": fixture_id, "pattern": pattern_id, "locked": bool(args.get("locked"))},
        args.get("at"),
    )
    state["fixtures"][fixture_id]["loaded_seq"] = ev["seq"]
    return {"fixture": copy.deepcopy(state["fixtures"][fixture_id]), "seq": ev["seq"]}


def cmd_fixture_lock(state: JSONDict, args: JSONDict) -> JSONDict:
    fixture = state["fixtures"].get(args["fixture"])
    if not fixture:
        raise HandOffError("empty_fixture", f"fixture {args['fixture']} has no pattern")
    fixture["locked"] = bool(args.get("locked", True))
    ev = _audit(
        state,
        "fixture_lock_changed",
        {"fixture": args["fixture"], "locked": fixture["locked"]},
        args.get("at"),
    )
    return {"fixture": copy.deepcopy(fixture), "seq": ev["seq"]}


def cmd_pattern_unload(state: JSONDict, args: JSONDict) -> JSONDict:
    fixture_id = args["fixture"]
    fixture = state["fixtures"].get(fixture_id)
    if not fixture:
        raise HandOffError("empty_fixture", f"fixture {fixture_id} is already empty")
    pattern_id = fixture["pattern_id"]
    pattern = state["patterns"][pattern_id]
    pattern["left_fixture"] = True
    pattern["unloaded_seq"] = state["seq"] + 1
    state["fixtures"].pop(fixture_id, None)
    ev = _audit(
        state,
        "pattern_left_fixture",
        {"fixture": fixture_id, "pattern": pattern_id, "rereadable": pattern["rereadable"]},
        args.get("at"),
    )
    pattern["unloaded_seq"] = ev["seq"]
    return {"pattern": pattern_id, "rereadable": pattern["rereadable"], "seq": ev["seq"]}


def cmd_order_create(state: JSONDict, args: JSONDict) -> JSONDict:
    order_id = args["id"]
    if order_id in state["orders"]:
        raise HandOffError("order_exists", f"order {order_id} already exists")
    order = {
        "id": order_id,
        "title": args.get("title", order_id),
        "pattern_id": args.get("pattern"),
        "fixture_id": args.get("fixture"),
        "entries": [],
        "reversals": [],
        "created_seq": state["seq"] + 1,
    }
    state["orders"][order_id] = order
    ev = _audit(state, "order_created", {k: order[k] for k in ("id", "pattern_id", "fixture_id")}, args.get("at"))
    order["created_seq"] = ev["seq"]
    return {"order": copy.deepcopy(order), "seq": ev["seq"]}


def _snapshot_fixture(state: JSONDict, fixture_id: str | None) -> JSONDict | None:
    if not fixture_id:
        return None
    fixture = state["fixtures"].get(fixture_id)
    if not fixture:
        return {"id": fixture_id, "empty": True}
    snap = copy.deepcopy(fixture)
    pattern = state["patterns"].get(snap.get("pattern_id"))
    if pattern:
        snap["rereadable"] = pattern.get("rereadable")
        snap["pattern_left_fixture"] = pattern.get("left_fixture")
    return snap


def _find_order_for_fixture(state: JSONDict, fixture_id: str | None) -> JSONDict | None:
    if not fixture_id:
        return None
    fixture = state["fixtures"].get(fixture_id)
    pattern_id = fixture.get("pattern_id") if fixture else None
    candidates = [
        o
        for o in state["orders"].values()
        if o.get("fixture_id") == fixture_id
        and (not pattern_id or o.get("pattern_id") == pattern_id)
    ]
    return candidates[-1] if candidates else None


def _check_provenance(state: JSONDict, args: JSONDict, reasons: list[str]) -> JSONDict:
    path = args.get("path", "original")
    interface = args.get("interface")
    provenance: JSONDict = {"path": path, "interface": interface}

    if path == "original":
        if not state["bus"].get("original_connected"):
            reasons.append("original_bus_disconnected")
        port = state["ports"].get(interface) if interface else None
        if not port or not port.get("installed_component"):
            reasons.append("no_installed_component")
            return provenance
        provenance["installed_component"] = port["installed_component"]
        if port.get("frozen"):
            reasons.append(f"interface_frozen:{port.get('freeze_reason') or 'policy'}")
        witness = port.get("witness") or {}
        reported = args.get("reported_serial", witness.get("reported_serial"))
        provenance["reported_serial"] = reported
        if args.get("reported_serial") and witness.get("reported_serial") and args["reported_serial"] != witness["reported_serial"]:
            reasons.append("input_source_differs_from_witness")
        if not witness.get("ok"):
            reasons.append(witness.get("reason") or "interface_not_witnessed")
            return provenance
        seal = _find_seal(
            state,
            witness["reported_serial"],
            interface,
            args.get("capability", ""),
        )
        if not seal:
            reasons.append("custodian_seal_no_longer_active")
            return provenance
        provenance.update(
            {
                "component": witness["reported_serial"],
                "witness_seq": witness["seq"],
                "seal_id": seal["id"],
                "circuit": seal.get("circuit") or port.get("circuit"),
                "continuous_since_seq": port.get("install_seq"),
            }
        )
        return provenance

    if path == "backup":
        bus = state["bus"]
        if not bus.get("backup_connected"):
            reasons.append("backup_bus_disconnected")
        if not bus.get("backup_board"):
            reasons.append("backup_board_not_identified")
        if not bus.get("backup_component"):
            reasons.append("backup_slot_empty")
        component = args.get("component") or bus.get("backup_component")
        provenance["component"] = component
        provenance["backup_board"] = bus.get("backup_board")
        if bus.get("backup_component") and component != bus["backup_component"]:
            reasons.append("component_not_in_backup_slot")
        seal = _find_seal(state, component, interface, args.get("capability", "")) if component else None
        if not seal:
            reasons.append("no_temporary_or_original_seal_for_backup_channel")
        else:
            provenance.update({"seal_id": seal["id"], "witness_seq": None})
        return provenance

    reasons.append(f"unknown_path:{path}")
    return provenance


def _check_context(
    state: JSONDict, args: JSONDict, reasons: list[str]
) -> tuple[JSONDict | None, JSONDict | None, str | None]:
    capability = args.get("capability")
    fixture_id = args.get("fixture")
    order_id = args.get("order")
    fixture_snapshot = _snapshot_fixture(state, fixture_id)

    order = state["orders"].get(order_id) if order_id else _find_order_for_fixture(state, fixture_id)
    effective_order_id = order_id or (order["id"] if order else None)

    if not order:
        reasons.append("no_named_order_for_context")
    elif order_id and order["id"] != order_id:
        reasons.append("unknown_order")

    if capability == "read_sample":
        if not fixture_snapshot or fixture_snapshot.get("empty"):
            reasons.append("fixture_has_no_pattern_at_arrival")
        else:
            if not fixture_snapshot.get("locked"):
                reasons.append("fixture_not_locked_at_arrival")
            if fixture_snapshot.get("pattern_left_fixture") and not fixture_snapshot.get("rereadable"):
                reasons.append("volatile_pattern_already_left_fixture")

    if order and fixture_snapshot and not fixture_snapshot.get("empty"):
        if order.get("fixture_id") and order["fixture_id"] != fixture_snapshot.get("id"):
            reasons.append("order_fixture_differs_from_arrival_fixture")
        if order.get("pattern_id") and order["pattern_id"] != fixture_snapshot.get("pattern_id"):
            reasons.append("arrival_pattern_differs_from_named_order")
    elif order and capability == "read_sample" and not fixture_snapshot:
        reasons.append("cannot_match_order_without_fixture_context")

    return fixture_snapshot, order, effective_order_id


def cmd_input(state: JSONDict, args: JSONDict) -> JSONDict:
    capability = args.get("capability", "read_sample")
    interface = args.get("interface")
    if not interface and args.get("path", "original") == "original":
        raise HandOffError("missing_interface", "input requires an interface")

    reasons: list[str] = []
    provenance = _check_provenance(state, args, reasons)
    fixture_snapshot, order, effective_order_id = _check_context(state, args, reasons)

    raw = {
        "interface": interface,
        "path": args.get("path", "original"),
        "capability": capability,
        "value": args.get("value"),
        "reported_serial": args.get("reported_serial"),
        "component": args.get("component"),
        "order_requested": args.get("order"),
        "fixture_requested": args.get("fixture"),
        "metadata": args.get("metadata", {}),
    }

    if not reasons and order:
        entry_id = _next_id(state, "entry", "E")
        entry = {
            "id": entry_id,
            "seq": state["seq"] + 1,
            "observed_at": args.get("at") or now_iso(),
            "status": "formal",
            "order_id": order["id"],
            "interface": interface,
            "path": raw["path"],
            "capability": capability,
            "value": args.get("value"),
            "component": provenance.get("component"),
            "evidence": provenance,
            "fixture": fixture_snapshot,
        }
        ev = _audit(state, "formal_input_accepted", {"entry_id": entry_id, "order": order["id"]}, args.get("at"))
        entry["seq"] = ev["seq"]
        state["entries"].append(entry)
        order["entries"].append({"entry_id": entry_id, "seq": ev["seq"], "value": entry["value"], "status": "formal"})
        return {
            "ok": True,
            "decision": "formal",
            "entry_id": entry_id,
            "order_id": order["id"],
            "seq": ev["seq"],
            "message": "input attributed and written to named work order",
        }

    # A physically disconnected path cannot deliver an input to the service.
    if any(r.endswith("bus_disconnected") for r in reasons):
        rejected = {
            "seq": state["seq"] + 1,
            "raw": raw,
            "reasons": reasons,
            "fixture": fixture_snapshot,
        }
        ev = _audit(state, "input_rejected", {"reasons": reasons}, args.get("at"))
        rejected["seq"] = ev["seq"]
        state["rejected_inputs"].append(rejected)
        return {"ok": False, "decision": "rejected", "reasons": reasons, "seq": ev["seq"]}

    preserve_caps = {"read_sample", "read_specimen", "scan_sample", "turn_page", "page_turn"}
    discardable = bool(args.get("discardable")) or capability not in preserve_caps
    qid = _next_id(state, "quarantine", "Q")
    item = {
        "id": qid,
        "seq": state["seq"] + 1,
        "status": "unclaimed",
        "raw": raw,
        "reasons": reasons,
        "provenance_at_arrival": provenance,
        "fixture_at_arrival": fixture_snapshot,
        "order_candidate": effective_order_id,
        "retention": "discardable" if discardable else "retained",
        "retention_reason": (
            "low-value unrecognized event; may be pruned after review"
            if discardable
            else "preserve: sample/work operation may be claimable and reread or reconstruction may be costly"
        ),
        "claim_attempts": [],
    }
    ev = _audit(state, "input_quarantined", {"quarantine_id": qid, "reasons": reasons}, args.get("at"))
    item["seq"] = ev["seq"]
    state["quarantine"].append(item)
    return {
        "ok": True,
        "decision": "quarantined",
        "quarantine_id": qid,
        "reasons": reasons,
        "retention": item["retention"],
        "order_id": None,
        "seq": ev["seq"],
        "message": "input cached in unclaimed area; no named work order was changed",
    }


def cmd_buffer_read(state: JSONDict, args: JSONDict) -> JSONDict:
    bid = _next_id(state, "buffer", "B")
    fixture_id = args.get("fixture", "F1")
    fixture = _snapshot_fixture(state, fixture_id)
    pattern_id = args.get("pattern") or (fixture or {}).get("pattern_id")
    pattern = state["patterns"].get(pattern_id or "", {})
    item = {
        "id": bid,
        "status": "buffered",
        "interface": args.get("interface"),
        "path": args.get("path", "original"),
        "capability": args.get("capability", "read_sample"),
        "value": args.get("value"),
        "reported_serial": args.get("reported_serial"),
        "component": args.get("component"),
        "order": args.get("order"),
        "fixture": fixture_id,
        "pattern_id": pattern_id,
        "volatile": not pattern.get("rereadable", False),
        "created_seq": state["seq"] + 1,
    }
    ev = _audit(state, "read_buffered", {"buffer_id": bid, "volatile": item["volatile"]}, args.get("at"))
    item["created_seq"] = ev["seq"]
    state["buffered_reads"].append(item)
    return {"buffer_id": bid, "volatile": item["volatile"], "seq": ev["seq"]}


def cmd_collect(state: JSONDict, args: JSONDict) -> JSONDict:
    item = next((b for b in state["buffered_reads"] if b["id"] == args["buffer"]), None)
    if not item:
        raise HandOffError("unknown_buffer", f"no buffered read {args['buffer']}")
    if item["status"] != "buffered":
        raise HandOffError("buffer_not_available", f"{item['id']} is {item['status']}")
    item["status"] = "collected"
    item["collected_seq"] = state["seq"] + 1
    cmd = {
        "interface": item["interface"],
        "path": item["path"],
        "capability": item["capability"],
        "value": item["value"],
        "reported_serial": item["reported_serial"],
        "component": item.get("component"),
        "order": args.get("order", item.get("order")),
        "fixture": args.get("fixture", item.get("fixture")),
        "at": args.get("at"),
    }
    result = cmd_input(state, cmd)
    item["input_result"] = result
    return result


def _lose_buffered(state: JSONDict, path: str | None = None) -> list[JSONDict]:
    lost: list[JSONDict] = []
    for item in state["buffered_reads"]:
        if item.get("status") != "buffered":
            continue
        if path and item.get("path") != path:
            continue
        if not item.get("volatile"):
            continue
        item["status"] = "lost_on_bus_switch"
        loss_id = _next_id(state, "loss", "L")
        loss = {
            "id": loss_id,
            "buffer_id": item["id"],
            "value": item.get("value"),
            "pattern_id": item.get("pattern_id"),
            "interface": item.get("interface"),
            "reason": "original_bus_disconnected_before_volatile_read_was_retrieved",
            "seq": state["seq"] + 1,
        }
        state["losses"].append(loss)
        lost.append(loss)
    if lost:
        ev = _audit(state, "volatile_reads_lost", {"loss_ids": [x["id"] for x in lost]})
        for loss in lost:
            loss["seq"] = ev["seq"]
    return lost


def cmd_bus_disconnect_original(state: JSONDict, args: JSONDict) -> JSONDict:
    if not state["bus"].get("original_connected"):
        raise HandOffError("already_disconnected", "original bench path is already disconnected")
    potential = [
        b
        for b in state["buffered_reads"]
        if b.get("status") == "buffered" and b.get("path", "original") == "original" and b.get("volatile")
    ]
    if potential and not args.get("accept_loss"):
        raise ConfirmationRequired(
            "disconnecting the original bench will lose unretrieved volatile reading(s); pass accept_loss=true to proceed and record the loss",
            {"buffered_reads": [b["id"] for b in potential]},
        )
    lost = _lose_buffered(state, "original")
    state["bus"]["original_connected"] = False
    if not state["bus"]["backup_connected"]:
        state["bus"]["lever"] = "disconnected"
    ev = _audit(state, "original_bus_disconnected", {"lost": [x["id"] for x in lost]}, args.get("at"))
    return {"bus": _public_bus(state), "lost": lost, "seq": ev["seq"]}


def cmd_bus_connect_backup(state: JSONDict, args: JSONDict) -> JSONDict:
    if state["bus"].get("original_connected"):
        raise HandOffError(
            "lever_conflict",
            "the wiring lever still connects the original bench; disconnect it before inserting the backup board",
        )
    if args.get("board"):
        state["bus"]["backup_board"] = args["board"]
    state["bus"]["backup_connected"] = True
    state["bus"]["lever"] = "backup"
    ev = _audit(state, "backup_bus_connected", {"board": state["bus"].get("backup_board")}, args.get("at"))
    return {"bus": _public_bus(state), "seq": ev["seq"]}


def cmd_bus_disconnect_backup(state: JSONDict, args: JSONDict) -> JSONDict:
    if not state["bus"].get("backup_connected"):
        raise HandOffError("already_disconnected", "backup path is not connected")
    state["bus"]["backup_connected"] = False
    state["bus"]["lever"] = "disconnected"
    ev = _audit(state, "backup_bus_disconnected", {}, args.get("at"))
    return {"bus": _public_bus(state), "seq": ev["seq"]}


def cmd_bus_connect_original(state: JSONDict, args: JSONDict) -> JSONDict:
    if state["bus"].get("backup_connected"):
        raise HandOffError("lever_conflict", "disconnect the backup board before returning lever to original")
    state["bus"]["original_connected"] = True
    state["bus"]["lever"] = "original"
    ev = _audit(state, "original_bus_connected", {}, args.get("at"))
    return {"bus": _public_bus(state), "seq": ev["seq"]}


def cmd_bus_switch_backup(state: JSONDict, args: JSONDict) -> JSONDict:
    disconnect_args = {"accept_loss": args.get("accept_loss"), "at": args.get("at")}
    try:
        first = cmd_bus_disconnect_original(state, disconnect_args)
    except ConfirmationRequired:
        raise
    second = cmd_bus_connect_backup(state, {"board": args.get("board"), "at": args.get("at")})
    return {"disconnect": first, "connect": second, "bus": _public_bus(state)}


def cmd_backup_attach(state: JSONDict, args: JSONDict) -> JSONDict:
    current = state["bus"].get("backup_component")
    component_id = args["component"]
    if current and current != component_id and not args.get("replace"):
        raise HandOffError(
            "backup_slot_occupied",
            f"backup board can hold only one item; {current} is already attached",
            {"attached_component": current},
        )
    state["bus"]["backup_component"] = component_id
    if args.get("board"):
        state["bus"]["backup_board"] = args["board"]
    ev = _audit(state, "backup_component_attached", {"component": component_id}, args.get("at"))
    return {"bus": _public_bus(state), "seq": ev["seq"]}


def cmd_backup_detach(state: JSONDict, args: JSONDict) -> JSONDict:
    old = state["bus"].get("backup_component")
    state["bus"]["backup_component"] = None
    ev = _audit(state, "backup_component_detached", {"component": old}, args.get("at"))
    return {"detached": old, "bus": _public_bus(state), "seq": ev["seq"]}


def cmd_handoff_create(state: JSONDict, args: JSONDict) -> JSONDict:
    hid = args["id"]
    if hid in state["handoffs"]:
        raise HandOffError("handoff_exists", f"handoff {hid} already exists")
    handoff = {
        "id": hid,
        "worker": args.get("worker"),
        "subject": args.get("subject"),
        "mode": args.get("mode", "original"),
        "component": args.get("component"),
        "status": "awaiting_receipt",
        "created_seq": state["seq"] + 1,
        "receipt_id": None,
        "settled_seq": None,
    }
    state["handoffs"][hid] = handoff
    ev = _audit(state, "handoff_created", handoff, args.get("at"))
    handoff["created_seq"] = ev["seq"]
    return {"handoff": handoff, "seq": ev["seq"]}


def _affected_interfaces(state: JSONDict, args: JSONDict, receipt_seq: int) -> set[str]:
    explicit = args.get("interfaces") or []
    if isinstance(explicit, str):
        explicit = [x.strip() for x in explicit.split(",") if x.strip()]
    if explicit:
        return set(explicit)
    scope = args.get("scope", "specified")
    if scope in {"backplane", "all"}:
        return set(state["ports"].keys())
    if scope == "circuit":
        circuits = set(args.get("circuits") or [args.get("circuit")])
        return {name for name, p in state["ports"].items() if p.get("circuit") in circuits}
    return set()


def _reverse_entry(state: JSONDict, entry: JSONDict, receipt: JSONDict, receipt_seq: int) -> JSONDict:
    entry["status"] = "reversed"
    entry["reversal"] = {
        "seq": receipt_seq,
        "receipt_id": receipt["id"],
        "reason": "late_reset_receipt_invalidated_mapping_after_observed_input",
    }
    order = state["orders"].get(entry["order_id"])
    if order:
        order.setdefault("reversals", []).append({"entry_id": entry["id"], "seq": receipt_seq})
        for ref in order.get("entries", []):
            if ref.get("entry_id") == entry["id"]:
                ref["status"] = "reversed"
    qid = _next_id(state, "quarantine", "Q")
    item = {
        "id": qid,
        "seq": receipt_seq,
        "status": "unclaimed",
        "raw": {
            "interface": entry["interface"],
            "path": entry.get("path"),
            "capability": entry["capability"],
            "value": entry["value"],
            "component": entry.get("component"),
            "order_requested": entry.get("order_id"),
        },
        "reasons": ["retroactive_reset_receipt_invalidates_provenance"],
        "provenance_at_arrival": entry.get("evidence", {}),
        "fixture_at_arrival": entry.get("fixture"),
        "order_candidate": entry.get("order_id"),
        "retention": "retained",
        "retention_reason": "formal posting reversed after late receipt; preserve until claim or disposal",
        "original_formal_entry_id": entry["id"],
        "claim_attempts": [],
    }
    state["quarantine"].append(item)
    entry["moved_to_quarantine"] = qid
    return item


def cmd_receipt_record(state: JSONDict, args: JSONDict) -> JSONDict:
    receipt = {
        "id": args["id"],
        "kind": args.get("kind", "maintenance"),
        "scope": args.get("scope"),
        "status": args.get("status", "completed"),
        "handoff_id": args.get("handoff"),
        "effective_seq": args.get("effective_seq"),
        "payload": args.get("payload", {}),
        "received_seq": state["seq"] + 1,
        "received_at": args.get("at") or now_iso(),
    }
    ev = _audit(state, "receipt_received", {"id": receipt["id"], "kind": receipt["kind"]}, args.get("at"))
    receipt["received_seq"] = ev["seq"]
    if receipt["effective_seq"] is None:
        receipt["effective_seq"] = ev["seq"]
    else:
        receipt["effective_seq"] = int(receipt["effective_seq"])
    state["receipts"].append(receipt)

    handoff_result = None
    if receipt["handoff_id"]:
        handoff = state["handoffs"].get(receipt["handoff_id"])
        if not handoff:
            raise HandOffError("unknown_handoff", f"handoff {receipt['handoff_id']} not found")
        handoff["receipt_id"] = receipt["id"]
        handoff["settled_seq"] = ev["seq"]
        handoff["status"] = "honored" if receipt["status"] == "completed" else receipt["status"]
        handoff_result = handoff

    invalidated_ports: list[str] = []
    reversed_entries: list[JSONDict] = []
    quarantined: list[str] = []
    if receipt["kind"] == "reset":
        affected = _affected_interfaces(state, args, ev["seq"])
        effective_seq = int(receipt["effective_seq"])
        for name in affected:
            port = _ensure_port(state, name)
            witness = port.get("witness")
            if witness and witness.get("ok") and int(witness.get("seq", 0)) < effective_seq:
                witness["ok"] = False
                witness["invalidated_by_receipt"] = receipt["id"]
                witness["reason"] = "reset_receipt_invalidated_pre_reset_mapping"
            post_reset_witness = bool(
                (port.get("witness") or {}).get("ok")
                and int((port.get("witness") or {}).get("seq", 0)) > effective_seq
            )
            if not post_reset_witness:
                port["trust_status"] = "reset_invalidated"
                port["frozen"] = True
                port["freeze_reason"] = f"reset receipt {receipt['id']} invalidated old channel mapping"
                invalidated_ports.append(name)

        for entry in state["entries"]:
            if entry.get("status") != "formal":
                continue
            if entry.get("interface") not in affected:
                continue
            if int(entry.get("seq", 0)) <= effective_seq:
                continue
            witness_seq = int(entry.get("evidence", {}).get("witness_seq") or 0)
            if witness_seq > effective_seq:
                continue  # valid post-reset re-witness explains this entry
            q = _reverse_entry(state, entry, receipt, ev["seq"])
            reversed_entries.append({"entry_id": entry["id"], "quarantine_id": q["id"]})
            quarantined.append(q["id"])
        if reversed_entries:
            _audit(
                state,
                "formal_entries_reversed_after_receipt",
                {"receipt": receipt["id"], "reversals": reversed_entries},
                args.get("at"),
            )

    return {
        "receipt": receipt,
        "handoff": handoff_result,
        "invalidated_ports": invalidated_ports,
        "reversed_entries": reversed_entries,
        "quarantined": quarantined,
        "seq": ev["seq"],
    }


def cmd_attestation_add(state: JSONDict, args: JSONDict) -> JSONDict:
    attestation = {
        "id": args["id"],
        "for_quarantine": args.get("quarantine"),
        "component": args["component"],
        "interface": args.get("interface"),
        "capability": args.get("capability"),
        "seal_id": args.get("seal"),
        "basis": args.get("basis", "custodian sealed continuous device log"),
        "seq": state["seq"] + 1,
    }
    state["attestations"].append(attestation)
    ev = _audit(state, "past_event_attested", attestation, args.get("at"))
    attestation["seq"] = ev["seq"]
    return {"attestation": attestation, "seq": ev["seq"]}


def _historical_context_allows_claim(item: JSONDict, order: JSONDict) -> list[str]:
    errors: list[str] = []
    snap = item.get("fixture_at_arrival") or {}
    if snap.get("empty") or not snap:
        errors.append("historical_fixture_context_missing")
    else:
        if order.get("fixture_id") and order["fixture_id"] != snap.get("id"):
            errors.append("historical_fixture_differs")
        if order.get("pattern_id") and order["pattern_id"] != snap.get("pattern_id"):
            errors.append("historical_pattern_differs")
        if item["raw"].get("capability") == "read_sample" and not snap.get("locked"):
            errors.append("reading_arrived_while_fixture_unlocked")
        if snap.get("pattern_left_fixture") and not snap.get("rereadable"):
            errors.append("volatile_pattern_had_already_left_fixture")
    return errors


def _source_identity_is_resolved(item: JSONDict) -> bool:
    """Whether arrival evidence already identifies the responsible component.

    A policy freeze is not an identity gap if the arrival witness/seal chain is
    complete; reset invalidation, mismatch, missing witness, etc. are gaps.
    """
    provenance = item.get("provenance_at_arrival") or {}
    if not (provenance.get("component") and provenance.get("seal_id")):
        return False
    identity_gap_markers = (
        "unverified",
        "not_entrusted",
        "no_custodian_seal",
        "no_installed_component",
        "source_differs",
        "identity_",
        "unknown_path",
        "backup_bus",
        "backup_slot",
        "component_not_in_backup",
        "no_temporary_or_original_seal",
        "retroactive_reset",
        "invalidates_provenance",
    )
    return not any(
        any(marker in reason for marker in identity_gap_markers)
        for reason in item.get("reasons", [])
    )


def cmd_claim(state: JSONDict, args: JSONDict) -> JSONDict:
    qid = args["quarantine"]
    item = next((q for q in state["quarantine"] if q["id"] == qid), None)
    if not item:
        raise HandOffError("unknown_quarantine_item", f"no unclaimed item {qid}")
    if item.get("status") != "unclaimed":
        raise HandOffError("already_resolved", f"{qid} is {item.get('status')}")

    order_id = args.get("order") or item.get("order_candidate")
    order = state["orders"].get(order_id) if order_id else None
    if not order:
        result = {"ok": False, "status": "claim_rejected", "reasons": ["unknown_or_missing_order"]}
        item["claim_attempts"].append({"seq": state["seq"] + 1, **result})
        _audit(state, "claim_rejected", {"quarantine_id": qid, "reasons": result["reasons"]}, args.get("at"))
        return result

    context_errors = _historical_context_allows_claim(item, order)

    source_errors: list[str] = [] if _source_identity_is_resolved(item) else [
        "source_identity_for_arrival_time_is_still_unresolved"
    ]
    attestation = None
    if source_errors:
        attestation_id = args.get("attestation")
        attestation = next((a for a in state["attestations"] if a["id"] == attestation_id), None)
        if not attestation:
            source_errors = ["source_identity_for_arrival_time_is_still_unresolved"]
        else:
            raw = item["raw"]
            if attestation.get("for_quarantine") not in (None, qid):
                source_errors = ["attestation_is_for_another_quarantine_item"]
            elif attestation.get("component") != args.get("component", attestation.get("component")):
                source_errors = ["attestation_component_mismatch"]
            elif attestation.get("interface") and attestation["interface"] != raw.get("interface"):
                source_errors = ["attestation_interface_mismatch"]
            elif attestation.get("capability") and attestation["capability"] != raw.get("capability"):
                source_errors = ["attestation_capability_mismatch"]
            else:
                source_errors = []

    all_errors = context_errors + source_errors
    attempt = {"seq": state["seq"] + 1, "order_id": order["id"], "attestation": args.get("attestation"), "errors": all_errors}
    item.setdefault("claim_attempts", []).append(attempt)
    if all_errors:
        ev = _audit(state, "claim_rejected", {"quarantine_id": qid, "errors": all_errors}, args.get("at"))
        attempt["seq"] = ev["seq"]
        return {"ok": False, "status": "claim_rejected", "quarantine_id": qid, "reasons": all_errors, "order_id": order["id"]}

    entry_id = _next_id(state, "entry", "E")
    entry = {
        "id": entry_id,
        "seq": state["seq"] + 1,
        "observed_seq": item["seq"],
        "observed_at": item.get("at"),
        "claimed_at": args.get("at") or now_iso(),
        "status": "formal_claim",
        "order_id": order["id"],
        "interface": item["raw"].get("interface"),
        "path": item["raw"].get("path"),
        "capability": item["raw"].get("capability"),
        "value": item["raw"].get("value"),
        "component": (attestation or {}).get("component") or item.get("provenance_at_arrival", {}).get("component"),
        "evidence": {
            "claimed_from_quarantine": qid,
            "attestation_id": (attestation or {}).get("id"),
            "arrival_provenance": item.get("provenance_at_arrival"),
        },
        "fixture": item.get("fixture_at_arrival"),
    }
    ev = _audit(state, "quarantine_claimed", {"quarantine_id": qid, "entry_id": entry_id, "order": order["id"]}, args.get("at"))
    entry["seq"] = ev["seq"]
    state["entries"].append(entry)
    order["entries"].append({"entry_id": entry_id, "seq": ev["seq"], "value": entry["value"], "status": "formal_claim"})
    item["status"] = "claimed"
    item["formal_entry_id"] = entry_id
    item["claimed_order_id"] = order["id"]
    return {"ok": True, "status": "claimed", "quarantine_id": qid, "entry_id": entry_id, "order_id": order["id"]}


def cmd_return_channel(state: JSONDict, args: JSONDict) -> JSONDict:
    interface = args["interface"]
    port = state["ports"].get(interface)
    witness = (port or {}).get("witness") or {}
    if not args.get("force") and not witness.get("ok"):
        raise HandOffError("channel_not_ready", f"channel {interface} must be re-witnessed before return")
    record = {
        "interface": interface,
        "component": args.get("component") or witness.get("reported_serial"),
        "from_path": args.get("from_path", "backup"),
        "to_path": "original",
        "seq": state["seq"] + 1,
        "note": args.get("note"),
    }
    state["channel_returns"].append(record)
    ev = _audit(state, "channel_returned", record, args.get("at"))
    record["seq"] = ev["seq"]
    return {"returned": record, "seq": ev["seq"]}


def cmd_advise(state: JSONDict, args: JSONDict) -> JSONDict:
    advice: list[str] = []
    stay_original_reasons: list[str] = []
    switch_blockers: list[str] = []

    volatile_buffered = [
        b["id"] for b in state["buffered_reads"]
        if b.get("status") == "buffered" and b.get("volatile") and b.get("path", "original") == "original"
    ]
    if volatile_buffered:
        stay_original_reasons.append("unretrieved volatile reading(s): " + ", ".join(volatile_buffered))
        switch_blockers.extend(volatile_buffered)

    for fixture_id, fixture in state["fixtures"].items():
        pattern = state["patterns"].get(fixture.get("pattern_id"), {})
        if not pattern.get("rereadable", True) and fixture.get("locked"):
            has_read = any(
                e.get("fixture", {}).get("pattern_id") == fixture.get("pattern_id")
                and e.get("status") == "formal"
                for e in state["entries"]
            ) or any(
                q.get("fixture_at_arrival", {}).get("pattern_id") == fixture.get("pattern_id")
                for q in state["quarantine"]
            )
            if not has_read:
                stay_original_reasons.append(f"{fixture_id} holds locked volatile pattern {fixture['pattern_id']} before any reading is safely received")

    unverified = [name for name, p in state["ports"].items() if p.get("frozen") or not (p.get("witness") or {}).get("ok")]
    if unverified:
        advice.append("Freeze affected circuits and cache their inputs; unaffected independently witnessed circuits may continue.")
    if stay_original_reasons:
        recommendation = "stay_original_until_volatile_reading_is_collected"
        advice.extend(stay_original_reasons)
        advice.append("Do not connect backup yet; collect the sample reading into formal ledger or quarantine first.")
    else:
        recommendation = "may_switch_to_backup_after_explicit_confirmation"
        advice.append("No unretrieved volatile original-path reading is known; switching to the entrusted backup board is defensible.")
    return {
        "recommendation": recommendation,
        "advice": advice,
        "switch_blockers": switch_blockers,
        "bus": _public_bus(state),
    }


def cmd_show(state: JSONDict, args: JSONDict) -> JSONDict:
    return {"state": copy.deepcopy(state)}


COMMANDS: dict[str, Callable[[JSONDict, JSONDict], JSONDict]] = {
    "reset": cmd_reset,
    "seed_demo": lambda s, a: seed_demo(s),
    "component_register": cmd_component_register,
    "seal_add": cmd_seal_add,
    "install": cmd_install,
    "remove": cmd_remove,
    "witness": cmd_witness,
    "freeze": cmd_freeze,
    "unfreeze": cmd_unfreeze,
    "pattern_register": cmd_pattern_register,
    "fixture_load": cmd_fixture_load,
    "fixture_lock": cmd_fixture_lock,
    "pattern_unload": cmd_pattern_unload,
    "order_create": cmd_order_create,
    "input": cmd_input,
    "buffer_read": cmd_buffer_read,
    "collect": cmd_collect,
    "bus_disconnect_original": cmd_bus_disconnect_original,
    "bus_connect_backup": cmd_bus_connect_backup,
    "bus_disconnect_backup": cmd_bus_disconnect_backup,
    "bus_connect_original": cmd_bus_connect_original,
    "bus_switch_backup": cmd_bus_switch_backup,
    "backup_attach": cmd_backup_attach,
    "backup_detach": cmd_backup_detach,
    "handoff_create": cmd_handoff_create,
    "receipt_record": cmd_receipt_record,
    "attestation_add": cmd_attestation_add,
    "claim": cmd_claim,
    "return_channel": cmd_return_channel,
    "advise": cmd_advise,
    "show": cmd_show,
}


def handle(state: JSONDict, command: JSONDict) -> JSONDict:
    """Dispatch one command against in-memory state.

    The state object is mutated. Persistence is the caller's responsibility so
    the HTTP service and CLI can share exactly the same policy implementation.
    """
    op = command.get("op") or command.get("command")
    if not op:
        raise HandOffError("missing_op", "command requires op")
    handler = COMMANDS.get(op)
    if handler is None:
        raise HandOffError("unknown_op", f"unknown operation {op}", {"known": sorted(COMMANDS)})
    args = dict(command)
    args.pop("op", None)
    args.pop("command", None)
    return handler(state, args)


def seed_demo(state: JSONDict) -> JSONDict:
    """Three field blocks/channels and two paper patterns."""
    # Three physical blocks: entrusted dual channel, replacement head, backup.
    handle(state, {"op": "component_register", "id": "BLK-DUO-1", "kind": "dual_knob_reader", "entrusted": True,
                   "capabilities": ["turn_page", "read_sample"]})
    handle(state, {"op": "component_register", "id": "BLK-READ-2", "kind": "replacement_reader", "entrusted": False,
                   "capabilities": ["read_sample"]})
    handle(state, {"op": "component_register", "id": "BLK-BACKUP-9", "kind": "backup_board", "entrusted": True,
                   "capabilities": ["backup_path"]})

    # The custodian seals exact component/interface/capability mappings.
    handle(state, {"op": "seal_add", "id": "SEAL-K-J1", "component": "BLK-DUO-1", "interface": "J1",
                   "circuit": "C-PAGE", "capability": "turn_page"})
    handle(state, {"op": "seal_add", "id": "SEAL-R-J2", "component": "BLK-DUO-1", "interface": "J2",
                   "circuit": "C-SCAN", "capability": "read_sample"})
    handle(state, {"op": "seal_add", "id": "SEAL-B-BUS", "component": "BLK-BACKUP-9", "interface": "BACKUP-BUS",
                   "circuit": "C-BACKUP", "capability": "backup_path"})

    handle(state, {"op": "install", "interface": "J1", "component": "BLK-DUO-1", "circuit": "C-PAGE"})
    handle(state, {"op": "install", "interface": "J2", "component": "BLK-DUO-1", "circuit": "C-SCAN"})
    handle(state, {"op": "witness", "interface": "J1", "capability": "turn_page"})
    handle(state, {"op": "witness", "interface": "J2", "capability": "read_sample"})

    handle(state, {"op": "pattern_register", "id": "P-VOLATILE", "rereadable": False})
    handle(state, {"op": "pattern_register", "id": "P-RELOAD", "rereadable": True})
    handle(state, {"op": "fixture_load", "fixture": "F1", "pattern": "P-VOLATILE", "locked": True})
    handle(state, {"op": "order_create", "id": "WO-V", "pattern": "P-VOLATILE", "fixture": "F1",
                   "title": "volatile specimen order"})
    handle(state, {"op": "order_create", "id": "WO-R", "pattern": "P-RELOAD", "fixture": "F1",
                   "title": "reloadable pattern order"})

    state["bus"]["backup_board"] = "BLK-BACKUP-9"
    return {"seeded": "bench3", "components": 3, "patterns": 2}
