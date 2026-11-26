"""Scripted walkthroughs and the two branch replays."""

from __future__ import annotations

from typing import Any

from .domain import NEW_STATION
from .engine import Engine
from .errors import DrillError
from .planner import build_plan
from .store import Store


def _attempt(label: str, fn):
    try:
        result = fn()
        brief = None
        if isinstance(result, dict):
            if "voucher" in result:
                v = result["voucher"]
                brief = {
                    "voucher_id": v and v.get("id"),
                    "status": v and v.get("status"),
                    "resigned": result.get("resigned"),
                    "replayed": result.get("replayed"),
                    "new_acceptances": result.get("new_acceptances"),
                    "missing_cabinets": result.get("missing_cabinets"),
                }
            elif "claimed" in result:
                brief = result
            else:
                brief = {"ok": True}
        return {"label": label, "ok": True, "brief": brief}
    except DrillError as exc:
        return {
            "label": label,
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }


def _compact_order(item: dict[str, Any]) -> dict[str, Any]:
    stations = item["stations"]
    return {
        "order_id": item["order_id"],
        "title": item["title"],
        "required": item["required_cabinets"],
        "status": item["status"],
        "common_station": item["common_station"],
        "common_station_label": item["common_station_label"],
        "materials_ready": item["materials_ready"],
        "within_deadline": item["within_deadline"],
        "overdue": item["overdue"],
        "voucher_status": item["voucher_status"],
        "new_can_claim": stations["new-station"]["can_claim"],
        "new_can_complete": stations["new-station"]["can_complete_now"],
        "new_can_half": stations["new-station"]["can_collect_half_now"],
        "new_can_resume": stations["new-station"]["can_resume_half"],
        "old_can_claim": stations["old-station"]["can_claim"],
        "old_can_complete": stations["old-station"]["can_complete_now"],
        "old_can_half": stations["old-station"]["can_collect_half_now"],
        "old_can_resume": stations["old-station"]["can_resume_half"],
    }


def frame(engine: Engine, label: str, attempts=None, notes=None) -> dict[str, Any]:
    matrix = engine.order_matrix()
    snap_auth = matrix["authority"]
    return {
        "tick": engine.tick,
        "shift": engine.authority_snapshot()["shift"],
        "label": label,
        "attempts": attempts or [],
        "notes": notes or [],
        "authority": {
            code: {
                "station": auth["station"],
                "station_label": auth["station_label"],
                "generation": auth["generation"],
                "basis": auth["basis"],
                "basis_label": auth["basis_label"],
            }
            for code, auth in snap_auth.items()
        },
        "orders": [_compact_order(o) for o in matrix["orders"]],
        "open_todos": engine.todos(open_only=True),
    }


def run_empty_walkthrough(db_path: str) -> dict[str, Any]:
    """Two empty cabinets -> denied unsafe open -> durable success -> half/full joint."""

    store = Store(db_path)
    engine = Engine(store)
    checks: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str):
        checks.append({"name": name, "ok": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name)

    engine.reset("empty")
    engine.snapshot_image("last-night-station-image", "IMG-LAST-NIGHT")
    frames.append(frame(engine, "两个空柜的初始状态"))

    engine.create_order(
        {
            "id": "O-DRAFT-CHANGED",
            "title": "先单柜、后追加乙柜的演练请求",
            "cabinets": ["A"],
            "material_ready_tick": 0,
            "deadline_tick": 3,
        }
    )
    engine.deliver_switch("A")
    auth = engine.authority_snapshot()["authority"]["A"]
    check(
        "delivered_without_receipt_keeps_old",
        auth["station"] == "old-station" and auth["basis"].startswith("old_lease"),
        auth["basis_label"],
    )

    denied = _attempt("无回执且旧租约有效时，新站不得开甲柜", lambda: engine.issue_order("O-DRAFT-CHANGED", "new-station"))
    check("new_denied_without_receipt", not denied["ok"] and denied["code"] == "station_not_authorized", denied.get("message", ""))
    frames.append(frame(engine, "只有换代命令送达，没有停用回执", [denied]))

    engine.acknowledge_switch("A")
    auth = engine.authority_snapshot()["authority"]["A"]
    check(
        "durable_receipt_transfers_a",
        auth["station"] == "new-station" and auth["basis"] == "durable_deactivation_receipt",
        auth["basis_label"],
    )

    engine.create_order(
        {
            "id": "O-A-SUCCESS",
            "title": "持久停用后新站签发的甲柜成功单",
            "cabinets": ["A"],
            "delivery_site": "new-site",
            "material_ready_tick": 0,
            "deadline_tick": 1,
        }
    )
    issued = engine.issue_order("O-A-SUCCESS")
    check("single_a_completed", issued["voucher"]["status"] == "completed", "甲柜回执齐全，凭条办结")
    frames.append(frame(engine, "取得持久停用回执后，新站成功签发甲柜单", [_attempt("幂等重放甲柜成功单", lambda: engine.issue_order("O-A-SUCCESS"))]))

    # Restart before changing the request; durable facts must survive.
    state_after_restart = engine.restart_review()
    check("restart_keeps_completed_order", any(o["id"] == "O-A-SUCCESS" and o["status"] == "completed" for o in state_after_restart["orders"]), "重启后已办结单仍为 completed")
    replayed = engine.issue_order("O-A-SUCCESS")
    check(
        "completed_voucher_not_resigned",
        replayed["already_completed"] and replayed["resigned"] is False and len(replayed["voucher"]["acceptances"]) == 1,
        "重复请求只返回原凭条， acceptance 数量不增加",
    )

    engine.amend_order(
        "O-DRAFT-CHANGED",
        {"cabinets": ["A", "B"], "title": "客户请求改为还需乙柜共同接收"},
    )
    split_denied = _attempt("甲已归新、乙仍认旧代时尝试联签", lambda: engine.issue_order("O-DRAFT-CHANGED"))
    check("split_blocks_joint", not split_denied["ok"] and split_denied["code"] == "split_authority", split_denied.get("message", ""))
    frames.append(frame(engine, "请求改为甲乙联签，但两柜签发站分裂", [split_denied]))

    half = engine.issue_order("O-DRAFT-CHANGED", NEW_STATION)
    check(
        "only_a_half_does_not_complete",
        half["voucher"]["status"] == "partial"
        and [a["cabinet"] for a in half["voucher"]["acceptances"]] == ["A"]
        and half["order"]["open_todos"]
        and half["order"]["open_todos"][0]["kind"] == "waiting_for_generation_boundary",
        "甲的一张回条已持久保留，乙仍认旧代并留下待办",
    )
    frames.append(
        frame(
            engine,
            "客户改成两柜后：指定同一未来站，也只有甲半张，不能办结",
            [{"label": "新站签发联签并送柜", "ok": True, "brief": {"status": "partial, A only"}}],
        )
    )

    engine.successful_switch("B")
    resumed = engine.issue_order("O-DRAFT-CHANGED")
    check(
        "resume_adds_only_b_half",
        resumed["voucher"]["status"] == "completed"
        and [a["cabinet"] for a in resumed["voucher"]["acceptances"]] == ["A", "B"]
        and len(resumed["new_acceptances"]) == 1
        and resumed["new_acceptances"][0]["cabinet"] == "B",
        "乙持久换代后，重试没有重签甲半张，只补乙并办结",
    )
    frames.append(frame(engine, "乙取得持久停用回执后凭原待办补齐，联签办结"))

    signed_events_before = len(
        [e for e in engine.store.events() if e["kind"] == "voucher_signed"]
    )
    acceptance_receipts_before = engine.store.query_one(
        "SELECT COUNT(*) AS n FROM receipts WHERE kind='cabinet_acceptance'"
    )["n"]

    # Actually roll station tables back to the saved image.  The simulated
    # cabinets and their durable receipts are not rolled back with the image.
    engine.restore_image_claim(
        {"snapshot_id": "IMG-LAST-NIGHT", "station": "old-station"}
    )
    check(
        "image_restore_removes_station_orders",
        engine.store.query_one("SELECT COUNT(*) AS n FROM orders")["n"] == 0,
        "快照中的班初镜像没有今天的凭条投影",
    )

    engine.create_order(
        {
            "id": "O-A-SUCCESS",
            "title": "恢复镜像后重提的甲柜单",
            "cabinets": ["A"],
            "delivery_site": "new-site",
            "material_ready_tick": 0,
            "deadline_tick": 1,
        }
    )
    recovered_a = engine.issue_order("O-A-SUCCESS")
    check(
        "completed_a_recovered_not_resigned",
        recovered_a.get("recovered_from_image")
        and recovered_a.get("already_completed")
        and recovered_a["resigned"] is False,
        "柜侧持久回执找回已签收甲柜凭条，未产生第二次签发",
    )

    engine.create_order(
        {
            "id": "O-DRAFT-CHANGED",
            "title": "恢复镜像后重提的甲乙联签",
            "cabinets": ["A", "B"],
            "material_ready_tick": 0,
            "deadline_tick": 3,
        }
    )
    recovered_joint = engine.issue_order("O-DRAFT-CHANGED")
    signed_events_after = len(
        [e for e in engine.store.events() if e["kind"] == "voucher_signed"]
    )
    acceptance_receipts_after = engine.store.query_one(
        "SELECT COUNT(*) AS n FROM receipts WHERE kind='cabinet_acceptance'"
    )["n"]
    check(
        "joint_recovered_and_no_duplicate_receipts",
        recovered_joint.get("recovered_from_image")
        and recovered_joint["voucher"]["status"] == "completed"
        and signed_events_after == signed_events_before
        and acceptance_receipts_after == acceptance_receipts_before,
        "两柜旧回执恢复同一凭条；voucher_signed 与柜侧 acceptance receipt 数量均不增加",
    )
    frames.append(
        frame(
            engine,
            "真恢复班初镜像后：从柜侧持久账本找回旧凭条，不重签",
            [
                {"label": "重提 O-A-SUCCESS", "ok": True, "brief": {"status": "recovered-completed"}},
                {"label": "重提 O-DRAFT-CHANGED", "ok": True, "brief": {"status": "recovered-completed"}},
            ],
        )
    )

    store.close()
    return {
        "ok": all(c["ok"] for c in checks),
        "db_path": db_path,
        "checks": checks,
        "frames": frames,
    }


def run_branch(case: str, db_path: str, late_ack: bool = False) -> dict[str, Any]:
    """Replay A reachable / B missing, with B reconnecting or staying offline."""

    if case not in ("reconnect", "offline"):
        raise ValueError("case must be reconnect or offline")

    store = Store(db_path)
    engine = Engine(store)
    engine.reset("branch")
    frames: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def check(name, condition, detail=""):
        checks.append({"name": name, "ok": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name)

    frames.append(frame(engine, "班初前：两柜名义上都仍认旧代", notes=["备站镜像中的‘我是签发站’不作为授权依据"]))
    initial_plan = build_plan(engine)

    # The single shift-start contact window.
    engine.successful_switch("A")
    if late_ack:
        # Counter-factual: the same shift-start command was delivered, but its
        # durable receipt was not available until the mid-shift reconnect.
        engine.deliver_switch("B")
        b_note = "乙的班初换代命令曾送达但无回执；按‘同一命令迟到回执’反事实回放"
    else:
        engine.queue_switch("B")
        b_note = "乙班初失联，换代命令仅持久排队；按题面‘错过须等下班’，班中不开新换代窗口"
    frames.append(
        frame(
            engine,
            "班初唯一换代联络后",
            notes=[
                "甲取得持久停用回执，立即归新站 G2",
                b_note,
            ],
        )
    )

    engine.advance(to=1)
    a_attempt = _attempt("第一拍末：新站领发新址甲柜单", lambda: engine.issue_order("O-NEW-A"))
    check("new_a_order_completes", a_attempt["ok"] and a_attempt["brief"]["status"] == "completed")
    frames.append(frame(engine, "第一拍末：新址立即营业", [a_attempt]))

    engine.advance(to=2)
    if case == "reconnect":
        engine.set_route("B", True, "new")

    pre_notes = [
        "动作前能力矩阵：新站可办所有当前归新代的甲柜/新址单；旧站仍可经馆内线办乙柜单",
        "联签若不指定固定站，资格分裂；两站各自只能取一柜半张，而异站半张不能拼用",
    ]
    frames.append(frame(engine, "第二拍动作前：当刻哪一站能签哪一单", notes=pre_notes))

    reconnect_attempts = []
    if not late_ack:
        window_attempt = _attempt(
            "班中试图新送乙换代令（应被唯一窗口拒绝）",
            lambda: engine.deliver_switch("B"),
        )
        reconnect_attempts.append(window_attempt)
        check(
            "midshift_new_switch_blocked",
            not window_attempt["ok"] and window_attempt["code"] == "switch_window_closed",
        )

    if case == "reconnect":
        if late_ack:
            ack_attempt = _attempt("乙回联后补同一换代命令的持久回执", lambda: engine.acknowledge_switch("B"))
            reconnect_attempts.append(ack_attempt)
            joint_attempt = _attempt("第二拍材料齐：新站联签甲乙", lambda: engine.issue_order("O-JOINT-AB"))
            reconnect_attempts.append(joint_attempt)
        else:
            joint_attempt = _attempt(
                "第二拍：指定未来归新站，先签发并联签送柜",
                lambda: engine.issue_order("O-JOINT-AB", NEW_STATION),
            )
            reconnect_attempts.append(joint_attempt)
    else:
        joint_attempt = _attempt(
            "第二拍：指定未来归新站，先签发并联签送柜",
            lambda: engine.issue_order("O-JOINT-AB", NEW_STATION),
        )
        reconnect_attempts.append(joint_attempt)

    matrix = engine.order_matrix()
    joint = next(o for o in matrix["orders"] if o["order_id"] == "O-JOINT-AB")
    if late_ack and case == "reconnect":
        check("midshift_late_ack_completes_joint", joint["status"] == "completed")
        notes = ["乙中途回联并补到持久停用回执，两柜同归新站，联签可在班末前办结"]
    else:
        detail = engine.order_detail("O-JOINT-AB")
        acceptance_cabinets = [
            a["cabinet"] for a in (detail["voucher"]["acceptances"] if detail["voucher"] else [])
        ]
        check(
            "joint_gets_a_half_while_belongs_old",
            joint_attempt["ok"]
            and joint_attempt["brief"]["status"] == "partial"
            and acceptance_cabinets == ["A"]
            and bool(detail["open_todos"]),
        )
        notes = [
            "材料已齐；新站是甲柜当前站，也是尽早切柜后的同一未来站，因此联签凭条先取到甲的半张回条",
            "乙仍认旧代，不能由新站接受；半张回条和等待代次边界的待办已持久化",
            "乙回联只恢复线路；没有持久停用回执或下一班租约围栏前，乙的代次归属不变",
        ] if case == "reconnect" else [
            "乙一直离线且仍认旧代；新站只能取得甲的半张回条，乙半张留下待办",
            "旧站仍可经馆内线签乙柜，但不能把这张凭条换到旧站，否则破坏同一站联签",
        ]
    frames.append(frame(engine, "第二拍：乙" + ("中途回联" if case == "reconnect" else "仍离线"), reconnect_attempts, notes))

    engine.advance(to=3)
    t3_attempts = []
    if engine.order_detail("O-JOINT-AB")["status"] != "completed":
        t3_attempts.append(_attempt("班末再试联签", lambda: engine.issue_order("O-JOINT-AB")))
    frames.append(frame(engine, "本班末：旧租约 inclusive 有效至第3拍", t3_attempts))

    engine.advance(to=4)
    next_shift_attempts = []
    if engine.order_detail("O-JOINT-AB")["status"] != "completed":
        if case == "reconnect":
            next_shift_attempts.append(_attempt("下一班窗口：乙送达并取得持久停用回执", lambda: engine.successful_switch("B")))
        issue_joint = _attempt("下一班：按当前资格办理联签", lambda: engine.issue_order("O-JOINT-AB"))
        next_shift_attempts.append(issue_joint)

    if case == "reconnect":
        check("next_shift_joint_completes", engine.order_detail("O-JOINT-AB")["status"] == "completed")
    else:
        detail = engine.order_detail("O-JOINT-AB")
        check(
            "offline_keeps_a_half_and_todo",
            detail["status"] == "partial"
            and [a["cabinet"] for a in (detail["voucher"]["acceptances"] if detail["voucher"] else [])] == ["A"]
            and bool(detail["open_todos"]),
        )

    image_attempt = _attempt(
        "恢复镜像自我声明后重放旧请求",
        lambda: (
            engine.restore_image_claim({"station": "old-station", "snapshot": "last-night"}),
            engine.issue_order("O-NEW-A"),
        )[1],
    )
    next_shift_attempts.append(image_attempt)
    frames.append(frame(engine, "下一班/租约到期后的当刻", next_shift_attempts, [
        "无乙回执时，第4拍起可信租约围栏也把乙柜判给新站",
        "离线剧本中资格已归一，但乙回条仍取不到，因此保留甲半张和待办",
    ]))

    engine.restart_review()
    frames.append(frame(engine, "重启复查：SQLite 持久事实和待办仍在"))
    store.close()

    return {
        "ok": all(c["ok"] for c in checks),
        "case": case,
        "late_ack": late_ack,
        "db_path": db_path,
        "checks": checks,
        "initial_plan": initial_plan,
        "frames": frames,
    }


def run_old_plan(db_path: str) -> dict[str, Any]:
    """Alternative boundary: preserve old dual service, then global take-back."""

    store = Store(db_path)
    engine = Engine(store)
    engine.reset("branch")
    frames = []
    checks = []

    def check(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": detail})
        if not cond:
            raise AssertionError(name)

    # The alternative is available only if the new-site customer agrees to the
    # extra-shift wait; the original hard-patience order would be lost at beat 1.
    engine.amend_order("O-NEW-A", {"flexible_next_shift": True, "patience_tick": 4})
    frames.append(frame(engine, "选择旧站先办完双柜：班初不切断甲柜", notes=["新址单已改为同意多等一班；否则该选择会违反第一拍末等待期限"]))

    engine.advance(to=1)
    claim_new = _attempt("第一拍：新站尝试领新址甲柜单", lambda: engine.claim_order("O-NEW-A", "new-station"))
    claim_next = _attempt("新站拉取可签单", lambda: engine.claim_next("new-station"))
    check("new_has_no_cabinet_at_beat1", not claim_new["ok"] and claim_new["code"] == "station_not_authorized")
    frames.append(frame(engine, "第一拍末：新址不能营业，客人等至下一班", [claim_new, claim_next]))

    engine.advance(to=2)
    joint = _attempt("第二拍材料齐：旧站办理两柜联签", lambda: engine.issue_order("O-JOINT-AB", "old-station"))
    check("old_joint_completes", joint["ok"] and joint["brief"]["status"] == "completed")
    frames.append(frame(engine, "第二拍：旧站经馆内线取得两柜回条", [joint]))

    engine.advance(to=3)
    frames.append(frame(engine, "班末：联签已在期限前办结"))

    engine.advance(to=4)
    engine.set_route("B", True, "new")
    switch_a = _attempt("下一班：甲持久换代", lambda: engine.successful_switch("A"))
    switch_b = _attempt("下一班：乙持久换代", lambda: engine.successful_switch("B"))
    a_next = _attempt("下一班：新站交付新址甲柜单", lambda: engine.issue_order("O-NEW-A"))
    replay_old = _attempt("恢复镜像/重放旧联签", lambda: engine.issue_order("O-JOINT-AB"))
    check("deferred_a_completes_next_shift", a_next["ok"] and a_next["brief"]["status"] == "completed")
    check("old_joint_not_resigned", replay_old["ok"] and replay_old["brief"]["replayed"] and replay_old["brief"]["status"] == "completed")
    frames.append(frame(engine, "下一班整体接回；旧联签只幂等返回，不重签", [switch_a, switch_b, a_next, replay_old]))

    engine.restart_review()
    frames.append(frame(engine, "重启复查：两笔办结凭条均保持原状"))
    store.close()
    return {"ok": all(c["ok"] for c in checks), "db_path": db_path, "checks": checks, "frames": frames}
