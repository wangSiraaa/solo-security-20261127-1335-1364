"""Pure authority and scheduling rules for the drill."""

from __future__ import annotations

from typing import Any

OLD_STATION = "old-station"
NEW_STATION = "new-station"

OLD_GENERATION = 1
NEW_GENERATION = 2

STATION_LABELS = {
    OLD_STATION: "旧站",
    NEW_STATION: "新站/备站",
}

CABINET_LABELS = {
    "A": "甲柜",
    "B": "乙柜",
}

# The branch replay uses beat points 0..4:
# 0 = shift start / only generation-switch window
# 1 = end of first beat
# 2 = second beat, materials ready / B reconnect event
# 3 = end of trusted shift / old lease inclusive end
# 4 = next shift start; lease fence now belongs to the new generation.
DEFAULT_LEASE_THROUGH = 3
NEXT_SHIFT_TICK = 4
SWITCH_WINDOWS = (0, 4)


def station_label(station: str | None) -> str:
    if not station:
        return "无共同签发站"
    return STATION_LABELS.get(station, station)


def cabinet_label(code: str) -> str:
    return CABINET_LABELS.get(code, code)


def row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def derive_authority(cabinet: dict[str, Any] | Any, tick: int) -> dict[str, Any]:
    """Return who can sign this cabinet at ``tick``.

    A durable deactivation receipt dominates a still-valid nominal lease.
    Without one, the old station keeps authority through the trusted lease
    end.  After that point the trusted shift fence transfers authority to the
    new generation even if the cabinet remains offline.
    """

    if not isinstance(cabinet, dict):
        cabinet = row_dict(cabinet)

    code = cabinet["code"]
    old_gen = cabinet["old_generation"]
    new_gen = cabinet["new_generation"]
    receipt_id = cabinet.get("deactivation_receipt_id")
    lease_through = int(cabinet["lease_through_tick"])

    if receipt_id:
        return {
            "cabinet": code,
            "cabinet_label": cabinet_label(code),
            "station": NEW_STATION,
            "station_label": station_label(NEW_STATION),
            "generation": new_gen,
            "basis": "durable_deactivation_receipt",
            "basis_label": "已取得停用旧代的持久回执",
            "deactivation_receipt_id": receipt_id,
            "old_generation_accepted": False,
        }

    if tick <= lease_through:
        delivered_tick = cabinet.get("switch_delivered_tick")
        if cabinet.get("switch_state") == "DELIVERED":
            basis = "old_lease_valid_command_without_receipt"
            basis_label = "换代命令已送达但无停用回执，旧租约仍有效"
        else:
            basis = "valid_old_lease"
            basis_label = "无持久停用回执，旧租约仍按可信班次有效"
        return {
            "cabinet": code,
            "cabinet_label": cabinet_label(code),
            "station": OLD_STATION,
            "station_label": station_label(OLD_STATION),
            "generation": old_gen,
            "basis": basis,
            "basis_label": basis_label,
            "deactivation_receipt_id": None,
            "switch_state": cabinet.get("switch_state", "NONE"),
            "switch_delivered_tick": delivered_tick,
            "old_generation_accepted": True,
        }

    return {
        "cabinet": code,
        "cabinet_label": cabinet_label(code),
        "station": NEW_STATION,
        "station_label": station_label(NEW_STATION),
        "generation": new_gen,
        "basis": "trusted_lease_expiry",
        "basis_label": "旧租约已按可信班次到期，由代次围栏交回新代",
        "deactivation_receipt_id": None,
        "old_generation_accepted": False,
    }


def reachable(cabinet: dict[str, Any] | Any, station: str) -> bool:
    if not isinstance(cabinet, dict):
        cabinet = row_dict(cabinet)
    if station == OLD_STATION:
        # The old station may use the archive internal line while its lease is
        # valid; this is modelled independently from the new-site route.
        return bool(cabinet.get("internal_online", 1))
    if station == NEW_STATION:
        return bool(cabinet.get("new_route_online", 1))
    return False


def site_allows(station: str, delivery_site: str) -> bool:
    if delivery_site == "new-site":
        # A ticket limited to new-site delivery cannot be rescued by the old
        # station on the archive internal line.
        return station == NEW_STATION
    if delivery_site == "old-site":
        return station == OLD_STATION
    # archive-line / either fictional desk can be served by either station if
    # the required cabinets are reachable from that station's route.
    return station in (OLD_STATION, NEW_STATION)


def common_authority(
    required: list[str], authorities: dict[str, dict[str, Any]]
) -> tuple[str | None, list[dict[str, Any]]]:
    """All cabinets must point to one station for a joint voucher."""
    relevant = [authorities[code] for code in required]
    stations = {item["station"] for item in relevant}
    if len(stations) == 1:
        return relevant[0]["station"], relevant
    return None, relevant
