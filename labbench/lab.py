from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .bench import Bench
from .identity import HolderDevice, ProofOracle, Registry, School
from .privacy import PublicReceiptBook
from .store import new_id, utc_now

REQUIRED_QUALIFICATION = "practical-power"
COMMON_NAME = "张伟"


class Lab:
    """组合两所学校、身份合作方、公开回单簿和两台本地实验台。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        config_path = self.root / "lab_config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"实验台尚未初始化：{self.root}")
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.mode = self.config["mode"]

        self.schools = {
            "school_a": School("school_a", self.root / "schools"),
            "school_b": School("school_b", self.root / "schools"),
        }
        self.receipt_book = PublicReceiptBook(self.root / "public" / "receipts.jsonl")
        self.bench_a = Bench("bench_a", self.root)
        self.bench_b = Bench("bench_b", self.root)

        if self.mode == "holder":
            self.oracle = ProofOracle(self.root / "cardholder_oracle.json")
            self.registry = Registry(self.root / "registry", mode="holder")
            self.verifier_oracle = self.oracle
        elif self.mode == "registry":
            self.registry = Registry(self.root / "registry", mode="registry")
            self.oracle = self.registry.oracle
            self.verifier_oracle = self.oracle
        else:
            raise ValueError(f"未知合作模式：{self.mode}")

    @classmethod
    def create(cls, root: str | Path, *, mode: str = "holder", reset: bool = False) -> "Lab":
        root = Path(root)
        if reset and root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        (root / "lab_config.json").write_text(
            json.dumps({"mode": mode, "created_at": utc_now()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return cls(root)

    def school(self, school_id: str) -> School:
        return self.schools[school_id]

    def resolve_card(self, card_id: str) -> dict[str, Any] | None:
        for school in self.schools.values():
            card = school.find_card(card_id)
            if card is not None:
                return card
        return None

    def verify_identity(
        self, bundle: dict[str, Any], card_ids: list[str], bench_id: str, nonce: str
    ) -> bool:
        if not isinstance(bundle, dict) or bundle.get("mode") != self.mode:
            return False
        return self.verifier_oracle.verify_bundle(
            bundle, card_ids, bench_id, nonce
        )

    def graduate(
        self,
        school_id: str,
        student_local_id: str,
        name: str,
        *,
        holder: HolderDevice | None = None,
        course: str = "实操安全课程",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        school = self.school(school_id)
        card = school.issue_completion(
            student_local_id=student_local_id,
            name=name,
            course=course,
            qualification=REQUIRED_QUALIFICATION,
            idempotency_key=idempotency_key,
        )
        if self.mode == "holder":
            if holder is None:
                raise ValueError("持卡器模式下发证时必须由学员持卡器绑定")
            handle = holder.bind_card(card["card_id"], school_id)
            card = school.attach_holder_handle(card["card_id"], handle)
        return card

    def seed_named_cast(self) -> dict[str, Any]:
        """建立同名学员和同名同学的固定剧情角色。"""
        protagonist = None
        classmate = None
        if self.mode == "holder":
            protagonist = HolderDevice.create(self.root, label="protagonist")
            classmate = HolderDevice.create(self.root, label="same_name_classmate")

        a_card = self.graduate(
            "school_a",
            "A-1001",
            COMMON_NAME,
            holder=protagonist,
            course="高压实操甲班",
            idempotency_key="A-1001:practical-2026",
        )
        b_card = self.graduate(
            "school_b",
            "B-2046",
            COMMON_NAME,
            holder=protagonist,
            course="高压实操乙班",
            idempotency_key="B-2046:practical-2026",
        )
        classmate_b_card = self.graduate(
            "school_b",
            "B-2099",
            COMMON_NAME,
            holder=classmate,
            course="高压实操乙班",
            idempotency_key="B-2099:practical-2026",
        )

        link = None
        if self.mode == "registry":
            link = self.registry.witness_link(a_card, b_card)

        return {
            "protagonist": protagonist,
            "classmate": classmate,
            "a_card": a_card,
            "b_card": b_card,
            "classmate_b_card": classmate_b_card,
            "link_id": link["link_id"] if link else None,
        }

    def reissue_card(
        self,
        old_card_id: str,
        *,
        holder: HolderDevice | None = None,
        link_id: str | None = None,
        student_local_id: str | None = None,
    ) -> dict[str, Any]:
        old_card = self.resolve_card(old_card_id)
        if old_card is None:
            raise KeyError(old_card_id)
        school = self.school(old_card["school_id"])
        new_card = school.issue_completion(
            student_local_id=student_local_id or old_card["student_local_id"],
            name=old_card["name"],
            course=old_card["course"],
            qualification=old_card["qualification"],
        )
        if self.mode == "holder":
            if holder is None:
                raise ValueError("换卡到新持卡器时也必须由学员亲自绑定")
            handle = holder.bind_card(new_card["card_id"], school.school_id)
            new_card = school.attach_holder_handle(new_card["card_id"], handle)
        school.mark_replaced(old_card_id, new_card["card_id"])

        if self.mode == "registry":
            resolved_link = link_id or self.registry.store.read()["card_index"].get(old_card_id)
            if not resolved_link:
                raise ValueError("登记处模式换卡必须找到或提供关联编号")
            self.registry.replace_card(
                resolved_link, old_card["school_id"], old_card_id, new_card
            )
        return new_card

    def export_school(self, school_id: str, export_path: str | Path | None = None) -> dict[str, Any]:
        data = self.school(school_id).export_records()
        if export_path is not None:
            path = Path(export_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return data

    def export_schools(self, export_dir: str | Path) -> dict[str, Path]:
        export_dir = Path(export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            school_id: export_dir / f"{school_id}_export.json"
            for school_id in self.schools
        }
        for school_id, path in paths.items():
            self.export_school(school_id, path)
        return paths

    def export_registry(self, path: str | Path | None = None) -> dict[str, Any]:
        data = self.registry.export_registry()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return data
