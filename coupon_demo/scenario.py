"""Runnable local scenarios: clean happy path and the two-machine incident."""
from __future__ import annotations

from typing import Any

from .service import CouponService


def happy_path(service: CouponService) -> dict[str, Any]:
    """Register material -> independent source -> print -> receipt -> redeem."""
    service.register_material(
        lot_id="LOT-HAPPY", quantity=20, note="新来的防伪刮刮券纸/封包材料"
    )
    source = service.install_independent_source(
        "printer-c", source_id="SRC-HAPPY", seed="happy-independent-demo-seed"
    )
    printed = service.print_packet(
        stack_id="STACK-HAPPY",
        source_id=source["source_id"],
        material_lot_id="LOT-HAPPY",
        count=3,
        ticket_prefix="H",
        title="独立来源演示叠",
        purpose="demo",
        seal_id="SEAL-HAPPY",
    )
    receipt = service.deliver_to_counter(
        "STACK-HAPPY",
        counter_id="COUNTER-1",
        signed_by="柜台签收员小赵",
        receipt_id="RCPT-HAPPY",
    )
    service.claim_coupon(
        "H-001", holder_name="演示顾客", counter_id="COUNTER-1"
    )
    secret = next(
        item["secret"]
        for item in printed["print_manifest_once"]
        if item["ticket_no"] == "H-001"
    )
    redemption = service.redeem_coupon(
        "H-001", secret, counter_id="COUNTER-1", benefit="演示福利：一杯热饮"
    )
    return {
        "material_lot_id": "LOT-HAPPY",
        "source_id": source["source_id"],
        "segment_id": source["segment_id"],
        "stack_id": "STACK-HAPPY",
        "receipt_id": receipt["receipt_id"],
        "redeemed_ticket": "H-001",
        "redemption": redemption,
        "print_manifest_once": printed["print_manifest_once"],
    }


def incident_setup(service: CouponService) -> dict[str, Any]:
    """Create A's interrupted real-name stack and B restored from same backup."""
    service.register_material(
        lot_id="LOT-INCIDENT", quantity=100, note="甲机实名预订券与乙机活动券材料"
    )
    backup = service.create_shared_backup(
        backup_id="BACKUP-1367",
        seed="fixed-demo-backup-secret-state",
        segment_id="SEG-OLD-SHARED",
    )
    source_a = service.restore_source_from_backup(
        "BACKUP-1367", "printer-a", source_id="SRC-A-OLD"
    )
    source_b = service.restore_source_from_backup(
        "BACKUP-1367", "printer-b", source_id="SRC-B-OLD"
    )

    printed_a = service.print_packet(
        stack_id="STACK-A-RESERVED",
        source_id=source_a["source_id"],
        material_lot_id="LOT-INCIDENT",
        count=3,
        planned_count=6,
        ticket_nos=["A-RES-001", "A-RES-002", "A-RES-003"],
        title="甲机昨夜实名预订券（印到一半）",
        purpose="real_name_reservation",
        holder_names=["林预订", "陈已领", "周未领"],
        real_name=True,
        seal_id="SEAL-A-CABINET",
        note="夜间断电，计划六张仅印三张",
    )
    handoff = service.create_handoff(
        "STACK-A-RESERVED",
        handoff_id="HANDOFF-A-OLD",
        from_party="甲机/夜班",
        to_party="旧柜",
        complete=False,
        documents=["印制见证(末段前)"],
        missing=["最后连续使用记录复核", "旧交接单", "柜台收货回执"],
        signed_by="",
        note="回执未全",
    )
    return {
        "backup": backup,
        "source_a": source_a,
        "source_b": source_b,
        "printed_a": printed_a,
        "handoff": handoff,
    }


def build_full_correction_demo(service: CouponService) -> dict[str, Any]:
    """A complete run used by CLI/web seed: both follow-ups and recovery."""
    happy = happy_path(service)
    setup = incident_setup(service)
    a_manifest = setup["printed_a"]["print_manifest_once"]

    cabinet = service.visit_old_cabinet("STACK-A-RESERVED")
    service.complete_handoff(
        "HANDOFF-A-OLD",
        documents=["旧交接单-A01", "连续记录", "封条核验", "柜台回执"],
        signed_by="补签",
        counter_id="COUNTER-2",
    )
    a_receipt_row = service.conn.execute(
        "SELECT receipt_id FROM counter_receipts WHERE stack_id='STACK-A-RESERVED'"
    ).fetchone()
    a_receipt_id = a_receipt_row["receipt_id"]

    service.claim_coupon("A-RES-002", holder_name="陈已领", counter_id="COUNTER-2")
    a1_secret = next(x["secret"] for x in a_manifest if x["ticket_no"] == "A-RES-001")
    a1_redemption = service.redeem_coupon(
        "A-RES-001", a1_secret, counter_id="COUNTER-2", benefit="实名预订福利"
    )

    # B had already used the same restored state before the overlap was known.
    printed_b = service.print_packet(
        stack_id="STACK-B-OLD-REPRINT",
        source_id=setup["source_b"]["source_id"],
        material_lot_id="LOT-INCIDENT",
        count=3,
        ticket_nos=["B-EVT-101", "B-EVT-102", "B-EVT-103"],
        title="乙机临时活动普通券（误用旧段）",
        purpose="temporary_event",
        seal_id="SEAL-B-OLD",
    )

    finding = service.confirm_reprint_from_witnesses("SEG-OLD-SHARED")

    # Recovery: a genuinely new independent source and a clean replacement stack.
    new_source = service.install_independent_source(
        "printer-b",
        source_id="SRC-B-NEW",
        seed="brand-new-b-source-after-incident",
    )
    replacement = service.print_packet(
        stack_id="STACK-B-NEW",
        source_id=new_source["source_id"],
        material_lot_id="LOT-INCIDENT",
        count=3,
        ticket_prefix="N",
        title="乙机新独立来源更正叠",
        purpose="replacement",
        seal_id="SEAL-B-NEW",
    )

    # Only the counter receipt target is corrected to the other stack.
    retarget = service.retarget_counter_receipt(
        a_receipt_id,
        "STACK-B-NEW",
        reason="核查确认 COUNTER-2 实际签收的是新来源另一叠；A 叠留查",
        signed_by="柜台组长",
    )
    # Link the two salvageable real-name corrections to independent-source
    # replacement tickets. The already-honored A-RES-001 intentionally has no
    # replacement order.
    for original_ticket, new_ticket in (
        ("A-RES-002", "N-001"),
        ("A-RES-003", "N-002"),
    ):
        order = service.conn.execute(
            "SELECT replacement_id FROM replacement_orders WHERE original_ticket_no=?",
            (original_ticket,),
        ).fetchone()
        service.complete_replacement_order(
            order["replacement_id"],
            new_stack_id="STACK-B-NEW",
            new_ticket_no=new_ticket,
        )

    # The retarget moved the receipt to the new stack. It can now be claimed.
    service.claim_coupon("N-001", holder_name="陈已领（更正换发）", counter_id="COUNTER-2")
    n1_secret = next(
        x["secret"]
        for x in replacement["print_manifest_once"]
        if x["ticket_no"] == "N-001"
    )
    n1_redemption = service.redeem_coupon(
        "N-001", n1_secret, counter_id="COUNTER-2", benefit="更正后新活动福利"
    )

    return {
        "happy_path": happy,
        "incident": {
            "a_manifest_once": a_manifest,
            "b_manifest_once": printed_b["print_manifest_once"],
            "cabinet": cabinet["stack_id"],
            "a_receipt_id": a_receipt_id,
            "a1_redemption": a1_redemption,
            "finding": finding,
            "new_source_id": new_source["source_id"],
            "replacement_manifest_once": replacement["print_manifest_once"],
            "retargeted_receipt": retarget,
            "n1_redemption": n1_redemption,
        },
    }
