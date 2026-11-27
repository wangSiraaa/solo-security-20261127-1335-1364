"""把所选字节真正装入一个空目录，并留下可回溯的操作回单。

安装前在“此刻”重走一遍信任判定，而不是沿用画面上的旧状态：
字节可能在渲染之后被换掉。回单记录用户当时看到的画面行、听到的朗读句、
核验记录来源（文件路径、钥匙 id、签名）与实际落盘字节的哈希，
事后可用 receipts/ 里的记录查回“当时看到和听到的来源”。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .digest import file_sha256
from .model import Entry, Model, load_entries
from .renderer import Frame


class InstallError(Exception):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp_for_filename() -> str:
    # 秒级时间戳会同名覆盖，加微秒保证每次安装的回单各自独立可查。
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


@dataclass
class InstallResult:
    receipt_path: Path
    entry_id: str
    installed_path: Path
    sha256: str
    receipt: dict


def _fresh_verification(model: Model, entry_id: str) -> Entry:
    """丢弃缓存状态，从磁盘重新加载并定位条目。"""
    fresh = load_entries(model.selector_dir)
    for e in fresh:
        if e.entry_id == entry_id:
            return e
    raise InstallError(f"条目已不存在：{entry_id}")


def install(model: Model, entry_id: str, install_root: str | Path,
            frame: Frame) -> InstallResult:
    entry = _fresh_verification(model, entry_id)
    if entry.verification.status != "verified":
        raise InstallError(
            f"拒绝安装：校验席结论为 {entry.verification.label}（{entry.verification.reason}）"
        )

    install_root = Path(install_root)
    install_root.mkdir(parents=True, exist_ok=True)
    existing = [p for p in install_root.iterdir()]
    if existing:
        raise InstallError(
            f"安装目录必须为空：{install_root}（现有 {len(existing)} 个条目）"
        )

    src_bytes = entry.package_path.read_bytes()
    # 安装名只用 entry_id 与原始包文件名，不使用包内任何自填文字。
    target = install_root / entry.package_path.name
    target.write_bytes(src_bytes)
    on_disk = file_sha256(target)
    if on_disk != entry.sha256:
        target.unlink(missing_ok=True)
        raise InstallError("落盘后哈希不一致，已撤销安装")

    att_path = model.selector_dir / "attestations" / f"{entry.sha256}.json"
    att_doc = json.loads(att_path.read_text(encoding="utf-8"))

    receipt = {
        "schema": "install-receipt/v1",
        "time": _utc_now(),
        "selected_entry_id": entry.entry_id,
        "selection_basis": {
            "note": "选择依据为校验席签名记录，而非光标行号或包内自填文字",
            "verification_status": entry.verification.status,
            "attestation_file": str(att_path),
            "attestation_signature": att_doc.get("signature"),
            "signed_by_key_id": att_doc.get("signed_by_key_id"),
            "bound_publisher_id": entry.verification.publisher_id,
            "bound_publisher_display_name": entry.verification.publisher_display_name,
            "bound_sha256": entry.sha256,
        },
        "what_user_saw": {
            "frame_width": frame.width,
            "visual_lines": frame.visual_lines,
            "provenance": [
                [{"ch": ch, "source": src, "direction": d} for ch, src, d in row]
                for row in frame.provenance
            ],
        },
        "what_user_heard": {
            "speech_lines": frame.speech_lines,
        },
        "package_self_described_fields": {
            # 原样留存制包者填写的文字，仅用于事后对照，明确标注不可信。
            "trusted": False,
            "display_name": entry.display_name,
            "summary": entry.summary,
            "claimed_publisher": entry.claimed_publisher,
        },
        "installed": {
            "directory": str(install_root),
            "file": str(target),
            "size": target.stat().st_size,
            "sha256_on_disk": on_disk,
            "sha256_matches_attestation": on_disk == entry.sha256,
        },
        "byte_preview_hex": src_bytes[:32].hex(),
    }

    receipts_dir = model.selector_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / f"{_utc_stamp_for_filename()}_{entry.entry_id}.json"
    seq = 1
    while receipt_path.exists():  # 极端同微秒冲突下再加序号，绝不覆盖
        receipt_path = receipts_dir / (
            f"{_utc_stamp_for_filename()}_{entry.entry_id}_{seq}.json"
        )
        seq += 1
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return InstallResult(
        receipt_path=receipt_path,
        entry_id=entry.entry_id,
        installed_path=target,
        sha256=on_disk,
        receipt=receipt,
    )


def verify_receipt(receipt_path: str | Path, install_root: str | Path) -> list[str]:
    """事后核对：回单记录的字节与安装目录里的实际字节是否仍一致。"""
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    problems: list[str] = []
    info = receipt["installed"]
    target = Path(info["file"])
    if not target.exists():
        problems.append(f"安装文件不存在：{target}")
    else:
        now_hash = file_sha256(target)
        if now_hash != info["sha256_on_disk"]:
            problems.append("安装目录中的字节已变化，与回单哈希不符")
        if now_hash != receipt["selection_basis"]["bound_sha256"]:
            problems.append("安装字节与核验记录绑定的哈希不符")
    if not Path(receipt["selection_basis"]["attestation_file"]).exists():
        problems.append("回单引用的核验记录文件已缺失")
    return problems
