"""Stateful drill operations: cut-over, claiming, issuance and recovery."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .domain import (
    DEFAULT_LEASE_THROUGH,
    NEW_GENERATION,
    NEW_STATION,
    OLD_GENERATION,
    OLD_STATION,
    SWITCH_WINDOWS,
    cabinet_label,
    common_authority,
    derive_authority,
    reachable,
    site_allows,
    station_label,
)
from .errors import DrillError
from .store import Store


class Engine:
    def __init__(self, store: Store):
        self.store = store

    # ---------- basic helpers ----------

    @property
    def tick(self) -> int:
        return int(self.store.meta_get("tick", 0))

    def now(self, at: int | None = None) -> int:
        return self.tick if at is None else int(at)

    def _cabinet_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.store.query_all("SELECT * FROM cabinets ORDER BY code")]

    def _cabinet(self, code: str) -> dict[str, Any]:
        code = code.upper()
        row = self.store.query_one("SELECT * FROM cabinets WHERE code=?", (code,))
        if row is None:
            raise DrillError("unknown_cabinet", f"未知接收柜代号: {code}", 404)
        return dict(row)

    def authority_snapshot(self, at: int | None = None) -> dict[str, Any]:
        tick = self.now(at)
        cabinets = self._cabinet_rows()
        authority = {c["code"]: derive_authority(c, tick) for c in cabinets}
        return {
            "tick": tick,
            "shift": self._shift_name(tick),
            "archive_code": self.store.meta_get("archive_code"),
            "restored_image": self.store.meta_get("restored_image"),
            "switch_windows": list(self.store.meta_get("switch_windows", SWITCH_WINDOWS)),
            "cabinets": cabinets,
            "authority": authority,
        }

    @staticmethod
    def _shift_name(tick: int) -> str:
        if tick <= 3:
            return f"第1班/第{tick}拍点" if tick else "第1班/班初"
        return f"第2班/第{tick - 3}拍点"

    def advance(self, to: int | None = None, beats: int = 1) -> dict[str, Any]:
        current = self.tick
        target = to if to is not None else current + beats
        if target < current:
            raise DrillError("clock_cannot_move_backwards", "演练时钟不能倒拨")
        self.store.meta_set("tick", target)
        self.store.execute(
            """
            UPDATE orders SET status='queued'
             WHERE status='waiting' AND material_ready_tick <= ?
            """,
            (target,),
        )
        self.store.event("clock_advanced", {"from": current, "to": target}, tick=target)
        return self.authority_snapshot(target)

    # ---------- reset / image ----------

    def reset(self, scenario: str = "empty") -> dict[str, Any]:
        scenario = scenario or "empty"
        valid = {"empty", "branch", "branch-reconnect", "branch-offline"}
        if scenario not in valid:
            raise DrillError("unknown_scenario", f"未知演练剧本: {scenario}", 400)

        with self.store.transaction():
            for table in [
                "acceptances",
                "vouchers",
                "todos",
                "orders",
                "receipts",
                "cabinets",
                "events",
                "image_snapshots",
                "meta",
            ]:
                self.store.execute(f"DELETE FROM {table}")
            self.store.execute("DELETE FROM sqlite_sequence WHERE name IN ('todos','events')")

            self.store.meta_set("tick", 0)
            self.store.meta_set("archive_code", "ARCH-DRILL-01")
            self.store.meta_set("scenario", scenario)
            self.store.meta_set("switch_windows", list(SWITCH_WINDOWS))
            self.store.meta_set(
                "restored_image",
                {
                    "snapshot": "last-night",
                    "image_station_claim": OLD_STATION,
                    "text_claim": "我是签发站",
                    "authoritative": False,
                    "note": "镜像自我声明只用于展示，不参与签发资格推导",
                },
            )

            branch = scenario.startswith("branch")
            b_new_route = 0 if branch else 1
            cabinets = [
                ("A", "甲柜", 1),
                ("B", "乙柜", b_new_route),
            ]
            for code, label, new_route in cabinets:
                self.store.execute(
                    """
                    INSERT INTO cabinets(
                      code, label, old_generation, new_generation,
                      lease_through_tick, internal_online, new_route_online,
                      switch_state, updated_tick
                    ) VALUES(?, ?, ?, ?, ?, 1, ?, 'NONE', 0)
                    """,
                    (code, label, OLD_GENERATION, NEW_GENERATION, DEFAULT_LEASE_THROUGH, new_route),
                )

            self.store.event("reset", {"scenario": scenario}, tick=0)

            if branch:
                self.create_order(
                    {
                        "id": "O-NEW-A",
                        "title": "新址甲柜凭条",
                        "cabinets": ["A"],
                        "delivery_site": "new-site",
                        "material_ready_tick": 0,
                        "deadline_tick": 1,
                        "patience_tick": 1,
                        "flexible_next_shift": False,
                        "priority": 10,
                    }
                )
                self.create_order(
                    {
                        "id": "O-JOINT-AB",
                        "title": "两柜联签保管凭条",
                        "cabinets": ["A", "B"],
                        "delivery_site": "archive-line",
                        "material_ready_tick": 2,
                        "deadline_tick": 3,
                        "patience_tick": 3,
                        "flexible_next_shift": False,
                        "priority": 20,
                    }
                )
                # Capability probes for the mid-shift matrix.  They are not
                # automatically issued by the branch replay.
                self.create_order(
                    {
                        "id": "O-NOW-A",
                        "title": "第二拍新来的甲柜单",
                        "cabinets": ["A"],
                        "delivery_site": "new-site",
                        "material_ready_tick": 2,
                        "deadline_tick": 4,
                        "patience_tick": 4,
                        "priority": 30,
                    }
                )
                self.create_order(
                    {
                        "id": "O-OLD-B",
                        "title": "馆内线乙柜单",
                        "cabinets": ["B"],
                        "delivery_site": "archive-line",
                        "material_ready_tick": 2,
                        "deadline_tick": 3,
                        "patience_tick": 3,
                        "priority": 40,
                    }
                )

        return self.state()

    STATION_IMAGE_TABLES = ("orders", "vouchers", "acceptances", "todos")

    def snapshot_image(self, label: str = "manual", snapshot_id: str | None = None) -> dict[str, Any]:
        snapshot_id = snapshot_id or f"IMG-{uuid.uuid4().hex[:8].upper()}"
        tables = {}
        for table in self.STATION_IMAGE_TABLES:
            tables[table] = [dict(r) for r in self.store.query_all(f"SELECT * FROM {table}")]
        payload = {"tables": tables, "tick": self.tick}
        self.store.execute(
            "INSERT INTO image_snapshots(id, label, tick, payload) VALUES(?, ?, ?, ?)",
            (snapshot_id, label, self.tick, json.dumps(payload, ensure_ascii=False)),
        )
        self.store.event("image_snapshot_created", {"snapshot_id": snapshot_id, "label": label})
        return {"ok": True, "snapshot_id": snapshot_id, "label": label, "tick": self.tick}

    def list_image_snapshots(self) -> list[dict[str, Any]]:
        rows = self.store.query_all("SELECT id, label, tick, created_at FROM image_snapshots ORDER BY created_at")
        return [dict(r) for r in rows]

    def restore_image_claim(self, body: dict[str, Any]) -> dict[str, Any]:
        """Restore a station image.

        By default this only changes the stale self-claim.  With
        ``snapshot_id`` or ``rewind_station_projection=true`` it also rolls
        station-local business tables backwards, while deliberately leaving
        simulated cabinet state and cabinet-issued durable receipts intact.
        """

        snapshot_id = body.get("snapshot_id")
        wipe_projection = bool(body.get("rewind_station_projection") or body.get("wipe_station_projection"))
        restored_tables: list[str] = []

        with self.store.transaction():
            if snapshot_id:
                row = self.store.query_one(
                    "SELECT payload FROM image_snapshots WHERE id=?", (snapshot_id,)
                )
                if row is None:
                    raise DrillError("unknown_snapshot", f"没有镜像快照: {snapshot_id}", 404)
                payload = json.loads(row["payload"])
                for table in self.STATION_IMAGE_TABLES:
                    self.store.execute(f"DELETE FROM {table}")
                    rows = payload.get("tables", {}).get(table, [])
                    for item in rows:
                        columns = list(item.keys())
                        placeholders = ",".join("?" for _ in columns)
                        self.store.execute(
                            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                            [item[c] for c in columns],
                        )
                    restored_tables.append(table)
            elif wipe_projection:
                for table in self.STATION_IMAGE_TABLES:
                    self.store.execute(f"DELETE FROM {table}")
                    restored_tables.append(table)

            claim = {
                "snapshot": body.get("snapshot", snapshot_id or "restored-image"),
                "image_station_claim": body.get("station", OLD_STATION),
                "text_claim": body.get("text_claim", "我是签发站"),
                "authoritative": False,
                "rewound_station_tables": restored_tables,
                "note": "镜像自我声明不是授权；柜侧持久回执和可信租约围栏不随站镜像回滚",
            }
            self.store.meta_set("restored_image", claim)
            self.store.event("image_restored", claim)
        return self.state()

    def restart_review(self) -> dict[str, Any]:
        self.store.reopen()
        self.store.event("backend_restarted", {"durable": True})
        return self.state()

    # ---------- cabinet connectivity / switch ----------

    def set_route(self, code: str, online: bool, route: str = "new") -> dict[str, Any]:
        code = code.upper()
        cab = self._cabinet(code)
        if route in ("new", "new-route", "public"):
            column = "new_route_online"
        elif route in ("internal", "old", "archive-line"):
            column = "internal_online"
        elif route == "both":
            self.store.execute(
                "UPDATE cabinets SET new_route_online=?, internal_online=?, updated_tick=? WHERE code=?",
                (1 if online else 0, 1 if online else 0, self.tick, code),
            )
            self.store.event(
                "route_changed", {"cabinet": code, "route": "both", "online": online}
            )
            return self.authority_snapshot()
        else:
            raise DrillError("unknown_route", f"未知线路: {route}", 400)

        self.store.execute(
            f"UPDATE cabinets SET {column}=?, updated_tick=? WHERE code=?",
            (1 if online else 0, self.tick, code),
        )
        self.store.event(
            "route_changed", {"cabinet": code, "route": route, "online": online}
        )
        return self.authority_snapshot()

    def queue_switch(self, code: str) -> dict[str, Any]:
        code = code.upper()
        cab = self._cabinet(code)
        if cab["switch_state"] == "ACKED":
            return self.authority_snapshot()
        self.store.execute(
            """
            UPDATE cabinets
               SET switch_state='QUEUED', switch_origin_tick=COALESCE(switch_origin_tick, ?)
             WHERE code=?
            """,
            (self.tick, code),
        )
        self.store.event("switch_queued", {"cabinet": code})
        return self.authority_snapshot()

    def deliver_switch(self, code: str, force: bool = False) -> dict[str, Any]:
        """Mark a generation-switch command as delivered, not acknowledged."""
        code = code.upper()
        cab = self._cabinet(code)
        if cab["switch_state"] == "ACKED":
            return self.authority_snapshot()
        if cab["switch_state"] == "DELIVERED":
            # Retrying delivery of the same shift-start command is idempotent;
            # its late acknowledgement is handled by acknowledge_switch.
            return self.authority_snapshot()
        if not force and self.tick not in SWITCH_WINDOWS:
            next_window = next((t for t in SWITCH_WINDOWS if t > self.tick), None)
            raise DrillError(
                "switch_window_closed",
                "本班唯一换代联络窗口已过；未送达的新换代命令须等下一班，或显式 force 演练反事实",
                details={
                    "tick": self.tick,
                    "switch_windows": list(SWITCH_WINDOWS),
                    "next_window_tick": next_window,
                },
            )
        self.store.execute(
            """
            UPDATE cabinets
               SET switch_state='DELIVERED',
                   switch_origin_tick=COALESCE(switch_origin_tick, ?),
                   switch_delivered_tick=?
             WHERE code=?
            """,
            (self.tick, self.tick, code),
        )
        self.store.event(
            "switch_delivered_without_receipt",
            {"cabinet": code, "warning": "delivered is not deactivated"},
        )
        return self.authority_snapshot()

    def acknowledge_switch(self, code: str, receipt_id: str | None = None) -> dict[str, Any]:
        """Persist the cabinet's durable old-generation deactivation receipt."""
        code = code.upper()
        cab = self._cabinet(code)
        if cab["switch_state"] == "ACKED" and cab["deactivation_receipt_id"]:
            return self.authority_snapshot()
        if cab["switch_state"] != "DELIVERED":
            raise DrillError(
                "switch_not_delivered",
                "换代命令尚未送达，不能取得停用回执",
                details={"cabinet": code, "switch_state": cab["switch_state"]},
            )

        receipt_id = receipt_id or f"RCPT-SW-{code}-G{cab['old_generation']}-OFF"
        existing = self.store.query_one("SELECT id FROM receipts WHERE id=?", (receipt_id,))
        if existing is None:
            self.store.execute(
                """
                INSERT INTO receipts(id, kind, cabinet, station, generation, tick, payload)
                VALUES(?, 'switch_deactivation', ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    code,
                    NEW_STATION,
                    cab["new_generation"],
                    self.tick,
                    json.dumps(
                        {
                            "old_generation_deactivated": cab["old_generation"],
                            "new_generation_active": cab["new_generation"],
                            "durable": True,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        self.store.execute(
            """
            UPDATE cabinets
               SET switch_state='ACKED', deactivation_receipt_id=?, updated_tick=?
             WHERE code=?
            """,
            (receipt_id, self.tick, code),
        )
        self.store.event(
            "durable_deactivation_receipt",
            {"cabinet": code, "receipt_id": receipt_id},
        )
        return self.authority_snapshot()

    def successful_switch(self, code: str, force: bool = False) -> dict[str, Any]:
        """Convenience operation for a reachable cabinet in one request."""
        self.deliver_switch(code, force=force)
        return self.acknowledge_switch(code)

    # ---------- orders ----------

    @staticmethod
    def _required_cabinets(data: dict[str, Any]) -> list[str]:
        raw = data.get("cabinets")
        if raw is None and "required_cabinets" in data:
            raw = data["required_cabinets"]
        if isinstance(raw, str):
            raw = [part.strip().upper() for part in raw.replace("，", ",").split(",") if part.strip()]
        if not raw:
            kind = data.get("kind", "single")
            raw = ["A", "B"] if kind in ("joint", "double", "ab") else ["A"]
        required = [str(c).upper() for c in raw]
        if not 1 <= len(required) <= 2 or len(set(required)) != len(required):
            raise DrillError(
                "invalid_cabinets", "演练凭条只能选择一个或两个不同的虚构接收柜", 400
            )
        return required

    def create_order(self, data: dict[str, Any]) -> dict[str, Any]:
        required = self._required_cabinets(data)
        order_id = data.get("id") or data.get("order_id") or f"O-{uuid.uuid4().hex[:8].upper()}"
        if self.store.query_one("SELECT id FROM orders WHERE id=?", (order_id,)):
            raise DrillError("order_exists", f"请求代号已存在: {order_id}", 409)

        tick = self.tick
        ready = int(data.get("material_ready_tick", tick))
        deadline = int(data.get("deadline_tick", tick + 1))
        patience = data.get("patience_tick")
        patience = None if patience is None else int(patience)
        site = data.get("delivery_site") or data.get("site") or "archive-line"
        title = data.get("title") or f"{'两柜联签' if len(required) == 2 else '单柜'}凭条 {order_id}"
        status = "queued" if tick >= ready else "waiting"
        archive_code = data.get("archive_code") or self.store.meta_get(
            "archive_code", "ARCH-DRILL-01"
        )

        self.store.execute(
            """
            INSERT INTO orders(
              id, archive_code, title, required_cabinets, delivery_site,
              material_ready_tick, deadline_tick, patience_tick,
              flexible_next_shift, priority, status, idempotency_key,
              created_tick, payload
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                archive_code,
                title,
                json.dumps(required, ensure_ascii=False),
                site,
                ready,
                deadline,
                patience,
                1 if data.get("flexible_next_shift") else 0,
                int(data.get("priority", 100)),
                status,
                data.get("idempotency_key", f"idem-{order_id}"),
                tick,
                json.dumps(data.get("payload", {}), ensure_ascii=False),
            ),
        )
        self.store.event("order_created", {"order_id": order_id, "cabinets": required})
        return self.order_detail(order_id)

    def _order_row(self, order_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if row is None:
            raise DrillError("unknown_order", f"未知请求/单号: {order_id}", 404)
        order = dict(row)
        order["required_cabinets"] = json.loads(order["required_cabinets"])
        return order

    def list_orders(self) -> list[dict[str, Any]]:
        rows = self.store.query_all("SELECT id FROM orders ORDER BY created_tick, priority, id")
        return [self.order_detail(r["id"]) for r in rows]

    def amend_order(self, order_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Change a draft/open request while preserving any actual half receipt."""
        order = self._order_row(order_id)
        voucher = self._voucher_for_order(order_id)
        if order["status"] == "completed" or (voucher and voucher["status"] == "completed"):
            raise DrillError(
                "order_completed",
                "凭条已实际签收办结；不能通过改单或恢复镜像重签，只能另开新请求",
            )

        fields = []
        params: list[Any] = []
        if "cabinets" in data or "required_cabinets" in data:
            required = self._required_cabinets(data)
            accepted = {a["cabinet"] for a in (voucher["acceptances"] if voucher else [])}
            removed = accepted.difference(required)
            if removed:
                raise DrillError(
                    "cannot_remove_accepted_half",
                    "不能从已有半张回条的凭条中删掉已接受柜；应保留原回条并补其余柜",
                    details={"accepted": sorted(accepted), "removed": sorted(removed)},
                )
            fields.append("required_cabinets=?")
            params.append(json.dumps(required, ensure_ascii=False))
        for api_field, column in [
            ("delivery_site", "delivery_site"),
            ("site", "delivery_site"),
            ("material_ready_tick", "material_ready_tick"),
            ("deadline_tick", "deadline_tick"),
            ("patience_tick", "patience_tick"),
            ("title", "title"),
        ]:
            if api_field in data and not (api_field == "site" and "delivery_site" in data):
                fields.append(f"{column}=?")
                params.append(data[api_field])
        if "flexible_next_shift" in data:
            fields.append("flexible_next_shift=?")
            params.append(1 if data["flexible_next_shift"] else 0)

        if fields:
            params.append(order_id)
            self.store.execute(f"UPDATE orders SET {', '.join(fields)} WHERE id=?", params)
            self.store.event("order_amended", {"order_id": order_id, "fields": list(data.keys())})
        return self.order_detail(order_id)

    def order_detail(self, order_id: str) -> dict[str, Any]:
        order = self._order_row(order_id)
        order["flexible_next_shift"] = bool(order["flexible_next_shift"])
        order["voucher"] = self._voucher_for_order(order_id)
        order["open_todos"] = [
            dict(r)
            for r in self.store.query_all(
                "SELECT * FROM todos WHERE order_id=? AND status='open' ORDER BY id",
                (order_id,),
            )
        ]
        tick = self.tick
        order["overdue"] = tick > int(order["deadline_tick"]) and order["status"] != "completed"
        if order.get("patience_tick") is not None:
            order["patience_expired"] = (
                tick > int(order["patience_tick"]) and order["status"] != "completed"
            )
        return order

    def _voucher_for_order(self, order_id: str) -> dict[str, Any] | None:
        row = self.store.query_one("SELECT * FROM vouchers WHERE order_id=?", (order_id,))
        if row is None:
            return None
        voucher = dict(row)
        voucher["body"] = json.loads(voucher["body"])
        voucher["acceptances"] = [
            dict(r)
            for r in self.store.query_all(
                "SELECT * FROM acceptances WHERE voucher_id=? ORDER BY cabinet",
                (voucher["id"],),
            )
        ]
        voucher["todos"] = [
            dict(r)
            for r in self.store.query_all(
                "SELECT * FROM todos WHERE voucher_id=? ORDER BY id", (voucher["id"],)
            )
        ]
        return voucher

    def order_matrix(self, at: int | None = None) -> dict[str, Any]:
        tick = self.now(at)
        snap = self.authority_snapshot(tick)
        auths = snap["authority"]
        matrix = []
        for row in self.store.query_all("SELECT id FROM orders ORDER BY priority, created_tick"):
            order = self.order_detail(row["id"])
            required = order["required_cabinets"]
            common, relevant = common_authority(required, auths)
            ready = tick >= int(order["material_ready_tick"])
            within_deadline = tick <= int(order["deadline_tick"])
            station_views = {}
            cab_rows = {c["code"]: c for c in snap["cabinets"]}
            voucher = order.get("voucher")
            voucher_station = voucher["station"] if voucher else None
            accepted = {a["cabinet"] for a in (voucher["acceptances"] if voucher else [])}
            for station in (OLD_STATION, NEW_STATION):
                is_common = station == common
                route_ok = all(reachable(cab_rows[c], station) for c in required)
                site_ok = site_allows(station, order["delivery_site"])
                same_voucher_station = voucher_station in (None, station)
                authorized_cabinets = [
                    c for c in required if auths[c]["station"] == station
                ]
                reachable_pending = [
                    c
                    for c in authorized_cabinets
                    if c not in accepted and reachable(cab_rows[c], station)
                ]
                can_claim = bool(
                    is_common
                    and ready
                    and site_ok
                    and order["status"] not in ("completed",)
                )
                can_deliver = bool(can_claim and route_ok)
                can_collect_half = bool(
                    ready
                    and site_ok
                    and same_voucher_station
                    and reachable_pending
                    and order["status"] != "completed"
                )
                can_resume_half = bool(
                    voucher_station == station
                    and voucher
                    and voucher["status"] in ("partial", "signed")
                )
                station_views[station] = {
                    "station_label": station_label(station),
                    "common_authority": is_common,
                    "materials_ready": ready,
                    "site_allowed": site_ok,
                    "routes_reachable": route_ok,
                    "authorized_cabinets": authorized_cabinets,
                    "reachable_pending_cabinets": reachable_pending,
                    "voucher_station": voucher_station,
                    "can_claim": can_claim,
                    "can_complete_now": can_deliver,
                    "can_collect_half_now": can_collect_half,
                    "can_resume_half": can_resume_half,
                    "can_make_progress": can_deliver or can_collect_half or can_resume_half,
                    "can_sign_in_principle": is_common and site_ok,
                }
            matrix.append(
                {
                    "order_id": order["id"],
                    "title": order["title"],
                    "required_cabinets": required,
                    "required_labels": [cabinet_label(c) for c in required],
                    "delivery_site": order["delivery_site"],
                    "status": order["status"],
                    "assigned_station": order["assigned_station"],
                    "material_ready_tick": order["material_ready_tick"],
                    "deadline_tick": order["deadline_tick"],
                    "patience_tick": order.get("patience_tick"),
                    "flexible_next_shift": order.get("flexible_next_shift", False),
                    "tick": tick,
                    "materials_ready": ready,
                    "within_deadline": within_deadline,
                    "overdue": not within_deadline and order["status"] != "completed",
                    "common_station": common,
                    "common_station_label": station_label(common),
                    "authorities": relevant,
                    "stations": station_views,
                    "open_todos": order["open_todos"],
                    "voucher_status": order["voucher"]["status"] if order["voucher"] else None,
                    "voucher_station": voucher_station,
                    "accepted_cabinets": sorted(accepted),
                }
            )
        return {"tick": tick, "authority": auths, "orders": matrix}

    # ---------- claiming ----------

    def claim_next(self, station: str, at: int | None = None) -> dict[str, Any]:
        tick = self.now(at)
        if station not in (OLD_STATION, NEW_STATION):
            raise DrillError("unknown_station", f"未知站点代号: {station}", 400)
        matrix = self.order_matrix(tick)
        for item in matrix["orders"]:
            view = item["stations"][station]
            if view["can_claim"] and item["status"] in ("queued", "waiting"):
                return self.claim_order(item["order_id"], station, at=tick)
        return {"claimed": None, "tick": tick, "station": station}

    def claim_order(
        self, order_id: str, station: str | None = None, at: int | None = None
    ) -> dict[str, Any]:
        tick = self.now(at)
        order = self._order_row(order_id)
        if order["status"] == "completed":
            raise DrillError("order_completed", "凭条已实际签收办结，不能再次领单签发")
        if tick < int(order["material_ready_tick"]):
            raise DrillError(
                "materials_not_ready",
                "材料尚未收齐，不能领单签发",
                details={"material_ready_tick": order["material_ready_tick"], "tick": tick},
            )

        matrix = self.order_matrix(tick)
        item = next((x for x in matrix["orders"] if x["order_id"] == order_id), None)
        assert item is not None
        common = item["common_station"]
        if common is None:
            raise DrillError(
                "split_authority",
                "各柜当前由不同站持有有效代次，没有任何一站能联签这一单",
                details={"order_id": order_id, "authorities": item["authorities"]},
            )
        if station is not None and station != common:
            raise DrillError(
                "station_not_authorized",
                f"当前只有{station_label(common)}能签这些柜",
                details={"required_station": common, "authorities": item["authorities"]},
            )
        station = common
        if not site_allows(station, order["delivery_site"]):
            raise DrillError(
                "site_not_served",
                "该单限定的交付地址不由当前签发站服务",
                details={"station": station, "site": order["delivery_site"]},
            )

        self.store.execute(
            "UPDATE orders SET status='claimed', assigned_station=? WHERE id=?",
            (station, order_id),
        )
        self.store.event("order_claimed", {"order_id": order_id, "station": station}, tick=tick)
        return self.order_detail(order_id)

    # ---------- issuance ----------

    def _ledger_receipt(self, voucher_id: str, cabinet: str) -> dict[str, Any] | None:
        receipt_id = f"RCPT-ACC-{voucher_id}-{cabinet}"
        row = self.store.query_one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
        return dict(row) if row else None

    def _recover_voucher_from_cabinet_ledger(
        self, order: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Reconstruct a station image from cabinet-issued durable receipts.

        This is the drill's version of "restored an old image": the station
        projection may be gone, but deterministic cabinet acceptance receipts
        still prove what was actually signed.  They are rehydrated, never
        signed again.
        """

        order_id = order["id"]
        required = order["required_cabinets"]
        voucher_id = f"V-{order_id}"
        ledger = {code: self._ledger_receipt(voucher_id, code) for code in required}
        found = {code: row for code, row in ledger.items() if row}
        if not found:
            return None

        stations = {row["station"] for row in found.values()}
        if len(stations) != 1:
            raise DrillError(
                "inconsistent_cabinet_ledger",
                "柜侧持久回执显示不同签发站，拒绝重签或拼接凭条",
                details={"receipts": found},
            )
        station = next(iter(stations))
        all_found = len(found) == len(required)
        last_tick = max(int(row["tick"]) for row in found.values())
        generation = int(next(iter(found.values()))["generation"])
        status = "completed" if all_found else "partial"

        with self.store.transaction():
            if self.store.query_one("SELECT id FROM vouchers WHERE id=?", (voucher_id,)) is None:
                body = {
                    "archive_code": order["archive_code"],
                    "order_id": order_id,
                    "title": order["title"],
                    "station": station,
                    "station_label": station_label(station),
                    "required_cabinets": required,
                    "recovered_from_cabinet_ledger": True,
                    "note": "Station image was rewound; body reconstructed from durable cabinet receipts.",
                }
                self.store.execute(
                    """
                    INSERT INTO vouchers(
                      id, order_id, archive_code, station, generation, status,
                      idempotency_key, body, created_tick, updated_tick, completed_tick
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        voucher_id,
                        order_id,
                        order["archive_code"],
                        station,
                        generation,
                        status,
                        f"voucher-{order['idempotency_key']}",
                        json.dumps(body, ensure_ascii=False, sort_keys=True),
                        last_tick,
                        self.tick,
                        last_tick if all_found else None,
                    ),
                )

            for code, receipt in found.items():
                acceptance_id = f"ACC-{voucher_id}-{code}"
                self.store.execute(
                    """
                    INSERT OR IGNORE INTO acceptances(
                      id, voucher_id, order_id, cabinet, station, generation,
                      receipt_id, tick
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        acceptance_id,
                        voucher_id,
                        order_id,
                        code,
                        receipt["station"],
                        receipt["generation"],
                        receipt["id"],
                        receipt["tick"],
                    ),
                )

            for code in required:
                if code in found:
                    self.store.execute(
                        """
                        UPDATE todos SET status='resolved', resolved_tick=?
                         WHERE voucher_id=? AND cabinet=? AND status='open'
                        """,
                        (self.tick, voucher_id, code),
                    )
                else:
                    exists = self.store.query_one(
                        "SELECT id FROM todos WHERE voucher_id=? AND cabinet=? AND status='open'",
                        (voucher_id, code),
                    )
                    if exists is None:
                        self.store.execute(
                            """
                            INSERT INTO todos(order_id, voucher_id, cabinet, kind, detail, created_tick)
                            VALUES(?, ?, ?, 'missing_cabinet_acceptance', ?, ?)
                            """,
                            (
                                order_id,
                                voucher_id,
                                code,
                                f"镜像恢复后从柜侧账本找回部分回条；仍缺{cabinet_label(code)}",
                                self.tick,
                            ),
                        )

            if all_found:
                self.store.execute(
                    "UPDATE orders SET status='completed', assigned_station=? WHERE id=?",
                    (station, order_id),
                )
                self.store.execute(
                    "UPDATE vouchers SET status='completed', completed_tick=? WHERE id=?",
                    (last_tick, voucher_id),
                )
                self.store.execute(
                    "UPDATE todos SET status='resolved', resolved_tick=? WHERE order_id=? AND status='open'",
                    (self.tick, order_id),
                )
                self.store.event(
                    "voucher_recovered_completed_without_resigning",
                    {"voucher_id": voucher_id, "order_id": order_id},
                )
            else:
                self.store.execute(
                    "UPDATE orders SET status='partial', assigned_station=? WHERE id=?",
                    (station, order_id),
                )
                self.store.execute(
                    "UPDATE vouchers SET status='partial' WHERE id=?", (voucher_id,)
                )
                self.store.event(
                    "partial_voucher_recovered_from_ledger",
                    {"voucher_id": voucher_id, "found": sorted(found)},
                )

        return self._voucher_for_order(order_id)

    def issue_order(
        self, order_id: str, station: str | None = None, at: int | None = None
    ) -> dict[str, Any]:
        tick = self.now(at)
        requested_station = station
        order = self._order_row(order_id)

        existing = self._voucher_for_order(order_id)
        recovered_from_ledger = False
        if existing and existing["status"] == "completed":
            return {
                "ok": True,
                "replayed": True,
                "resigned": False,
                "already_completed": True,
                "message": "旧凭条已实际签收办结；幂等返回原凭条，未重新签发",
                "voucher": existing,
                "order": self.order_detail(order_id),
            }

        if existing is None:
            recovered = self._recover_voucher_from_cabinet_ledger(order)
            if recovered is not None:
                existing = recovered
                recovered_from_ledger = True
                if recovered["status"] == "completed":
                    return {
                        "ok": True,
                        "replayed": True,
                        "resigned": False,
                        "already_completed": True,
                        "recovered_from_image": True,
                        "new_voucher_signed": False,
                        "message": "站镜像已回滚，但柜侧持久回执证明旧凭条已签收；恢复原凭条，未重签",
                        "voucher": recovered,
                        "order": self.order_detail(order_id),
                    }

        if tick < int(order["material_ready_tick"]):
            raise DrillError(
                "materials_not_ready",
                "材料尚未收齐，不能签发",
                details={"material_ready_tick": order["material_ready_tick"], "tick": tick},
            )

        snap = self.authority_snapshot(tick)
        auths = snap["authority"]
        required = order["required_cabinets"]
        common, relevant = common_authority(required, auths)

        if existing is not None:
            station = existing["station"]
            if requested_station is not None and requested_station != station:
                raise DrillError(
                    "voucher_station_mismatch",
                    "已有半张回条属于另一签发站；不能换站重签同一张凭条",
                    details={"existing_station": station, "requested": requested_station},
                )
            if common is not None and common != station:
                raise DrillError(
                    "original_station_no_longer_authorized",
                    "镜像找回的半张/旧凭条属于另一站；当前代次边界已整体变化，不能由别站补签",
                    details={
                        "order_id": order_id,
                        "original_station": station,
                        "current_common_station": common,
                        "authorities": relevant,
                    },
                )
        else:
            if station is None:
                if common is None:
                    raise DrillError(
                        "split_authority",
                        "两柜分属两站；如要先取其中一柜的半张回条，必须指定未来负责联签的同一站",
                        details={"order_id": order_id, "authorities": relevant},
                    )
                station = common
            elif station not in (OLD_STATION, NEW_STATION):
                raise DrillError("unknown_station", f"未知站点代号: {station}", 400)
            elif not any(auths[code]["station"] == station for code in required):
                raise DrillError(
                    "station_not_authorized",
                    "请求的站点当前不持有任何一个所需柜的有效代次，不能签发",
                    details={"requested": station, "authorities": relevant},
                )

        if not site_allows(station, order["delivery_site"]):
            raise DrillError(
                "site_not_served",
                "该交付地址不允许由该签发站交付",
                details={"station": station, "site": order["delivery_site"]},
            )

        cabinet_rows = {c["code"]: c for c in snap["cabinets"]}
        created_voucher = False
        new_acceptances: list[dict[str, Any]] = []
        blocked_cabinets: list[dict[str, Any]] = []
        resumed = existing is not None

        with self.store.transaction():
            if existing is None:
                voucher_id = f"V-{order_id}"
                generation = auths[required[0]]["generation"]
                body = {
                    "archive_code": order["archive_code"],
                    "order_id": order_id,
                    "title": order["title"],
                    "station": station,
                    "station_label": station_label(station),
                    "required_cabinets": required,
                    "generation_policy": "each cabinet acceptance records its then-valid generation",
                    "issued_tick": tick,
                }
                self.store.execute(
                    """
                    INSERT INTO vouchers(
                      id, order_id, archive_code, station, generation, status,
                      idempotency_key, body, created_tick, updated_tick
                    ) VALUES(?, ?, ?, ?, ?, 'signed', ?, ?, ?, ?)
                    """,
                    (
                        voucher_id,
                        order_id,
                        order["archive_code"],
                        station,
                        generation,
                        f"voucher-{order['idempotency_key']}",
                        json.dumps(body, ensure_ascii=False, sort_keys=True),
                        tick,
                        tick,
                    ),
                )
                self.store.execute(
                    "UPDATE orders SET status='claimed', assigned_station=? WHERE id=?",
                    (station, order_id),
                )
                self.store.event(
                    "voucher_signed",
                    {"voucher_id": voucher_id, "order_id": order_id, "station": station},
                    tick=tick,
                )
                created_voucher = True
            else:
                voucher_id = existing["id"]
                station = existing["station"]

            for code in required:
                already = self.store.query_one(
                    "SELECT * FROM acceptances WHERE voucher_id=? AND cabinet=?",
                    (voucher_id, code),
                )
                if already:
                    continue

                auth = auths[code]
                if auth["station"] != station:
                    # The voucher has one fixed station.  This cabinet still
                    # recognizes another generation/station, so leave the
                    # missing half as a durable todo rather than mixing signers.
                    blocked_cabinets.append(
                        {
                            "cabinet": code,
                            "reason": "cabinet_recognizes_other_station",
                            "current_authority": auth,
                        }
                    )
                    open_todo = self.store.query_one(
                        """
                        SELECT id FROM todos
                         WHERE voucher_id=? AND cabinet=? AND status='open'
                        """,
                        (voucher_id, code),
                    )
                    if open_todo is None:
                        self.store.execute(
                            """
                            INSERT INTO todos(order_id, voucher_id, cabinet, kind, detail, created_tick)
                            VALUES(?, ?, ?, 'waiting_for_generation_boundary', ?, ?)
                            """,
                            (
                                order_id,
                                voucher_id,
                                code,
                                f"已由{station_label(station)}签发，但{cabinet_label(code)}当前只接受"
                                f"{auth['station_label']}G{auth['generation']}；等待持久换代回执或租约到期",
                                tick,
                            ),
                        )
                        self.store.event(
                            "half_receipt_waiting_for_boundary",
                            {"voucher_id": voucher_id, "cabinet": code, "current": auth},
                            tick=tick,
                        )
                    else:
                        self.store.execute(
                            "UPDATE todos SET kind='waiting_for_generation_boundary', detail=? WHERE id=?",
                            (
                                f"已由{station_label(station)}签发，但{cabinet_label(code)}当前只接受"
                                f"{auth['station_label']}G{auth['generation']}；等待持久换代回执或租约到期",
                                open_todo["id"],
                            ),
                        )
                    continue

                cab = cabinet_rows[code]
                if not reachable(cab, station):
                    blocked_cabinets.append(
                        {"cabinet": code, "reason": "route_unreachable", "station": station}
                    )
                    open_todo = self.store.query_one(
                        """
                        SELECT id FROM todos
                         WHERE voucher_id=? AND cabinet=? AND status='open'
                        """,
                        (voucher_id, code),
                    )
                    if open_todo is None:
                        self.store.execute(
                            """
                            INSERT INTO todos(order_id, voucher_id, cabinet, kind, detail, created_tick)
                            VALUES(?, ?, ?, 'missing_cabinet_acceptance', ?, ?)
                            """,
                            (
                                order_id,
                                voucher_id,
                                code,
                                f"{cabinet_label(code)}尚未由{station_label(station)}在G{auth['generation']}接受，保留半张回条待补",
                                tick,
                            ),
                        )
                        self.store.event(
                            "half_receipt_todo_created",
                            {"voucher_id": voucher_id, "missing_cabinet": code},
                            tick=tick,
                        )
                    else:
                        self.store.execute(
                            "UPDATE todos SET kind='missing_cabinet_acceptance', detail=? WHERE id=?",
                            (
                                f"代次边界已到：{cabinet_label(code)}应由{station_label(station)}在"
                                f"G{auth['generation']}接受，但当前线路不可达，保留半张回条待补",
                                open_todo["id"],
                            ),
                        )
                    continue

                receipt_id = f"RCPT-ACC-{voucher_id}-{code}"
                self.store.execute(
                    """
                    INSERT INTO receipts(id, kind, cabinet, station, generation, tick, idempotency_key, payload)
                    VALUES(?, 'cabinet_acceptance', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt_id,
                        code,
                        station,
                        auth["generation"],
                        tick,
                        f"accept-{voucher_id}-{code}",
                        json.dumps(
                            {"voucher_id": voucher_id, "durable": True},
                            ensure_ascii=False,
                        ),
                    ),
                )
                acceptance_id = f"ACC-{voucher_id}-{code}"
                self.store.execute(
                    """
                    INSERT INTO acceptances(
                      id, voucher_id, order_id, cabinet, station, generation,
                      receipt_id, tick
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        acceptance_id,
                        voucher_id,
                        order_id,
                        code,
                        station,
                        auth["generation"],
                        receipt_id,
                        tick,
                    ),
                )
                self.store.execute(
                    """
                    UPDATE todos SET status='resolved', resolved_tick=?
                     WHERE voucher_id=? AND cabinet=? AND status='open'
                    """,
                    (tick, voucher_id, code),
                )
                new_acceptances.append(
                    {"cabinet": code, "receipt_id": receipt_id, "generation": auth["generation"]}
                )
                self.store.event(
                    "cabinet_accepted",
                    {"voucher_id": voucher_id, "cabinet": code, "receipt_id": receipt_id},
                    tick=tick,
                )

            acceptance_rows = self.store.query_all(
                "SELECT cabinet FROM acceptances WHERE voucher_id=?", (voucher_id,)
            )
            accepted_codes = {r["cabinet"] for r in acceptance_rows}
            missing = [c for c in required if c not in accepted_codes]

            if not missing:
                status = "completed"
                completed_tick = tick
                self.store.execute(
                    """
                    UPDATE vouchers SET status='completed', updated_tick=?, completed_tick=?
                     WHERE id=?
                    """,
                    (tick, tick, voucher_id),
                )
                self.store.execute(
                    "UPDATE orders SET status='completed' WHERE id=?", (order_id,)
                )
                self.store.execute(
                    """
                    UPDATE todos SET status='resolved', resolved_tick=?
                     WHERE order_id=? AND status='open'
                    """,
                    (tick, order_id),
                )
                self.store.event(
                    "voucher_completed", {"voucher_id": voucher_id, "order_id": order_id}, tick=tick
                )
            elif accepted_codes:
                status = "partial"
                completed_tick = None
                self.store.execute(
                    "UPDATE vouchers SET status='partial', updated_tick=? WHERE id=?",
                    (tick, voucher_id),
                )
                self.store.execute(
                    "UPDATE orders SET status='partial' WHERE id=?", (order_id,)
                )
            else:
                status = "signed"
                completed_tick = None
                self.store.execute(
                    "UPDATE vouchers SET status='signed', updated_tick=? WHERE id=?",
                    (tick, voucher_id),
                )
                self.store.execute(
                    "UPDATE orders SET status='blocked' WHERE id=?", (order_id,)
                )

        voucher = self._voucher_for_order(order_id)
        return {
            "ok": True,
            "replayed": resumed and not new_acceptances,
            "resumed": resumed,
            "recovered_from_image": recovered_from_ledger,
            "new_voucher_signed": created_voucher,
            "resigned": False,
            "new_acceptances": new_acceptances,
            "blocked_cabinets": blocked_cabinets,
            "missing_cabinets": missing if "missing" in locals() else [],
            "message": (
                "凭条已办结，未重复签发"
                if status == "completed"
                else "已保留已取得的半张回条和待办，重试只补缺失柜"
            ),
            "voucher": voucher,
            "order": self.order_detail(order_id),
        }

    # ---------- aggregate state ----------

    def todos(self, open_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM todos"
        if open_only:
            sql += " WHERE status='open'"
        sql += " ORDER BY id"
        return [dict(r) for r in self.store.query_all(sql)]

    def state(self) -> dict[str, Any]:
        snap = self.authority_snapshot()
        return {
            "ok": True,
            "tick": self.tick,
            "scenario": self.store.meta_get("scenario"),
            "archive_code": self.store.meta_get("archive_code"),
            "restored_image": snap["restored_image"],
            "authority": snap["authority"],
            "cabinets": snap["cabinets"],
            "orders": self.list_orders(),
            "todos": self.todos(open_only=False),
            "receipts": [dict(r) for r in self.store.query_all("SELECT * FROM receipts ORDER BY id")],
            "events": self.store.events(),
        }
