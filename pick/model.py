"""选择器的数据模型与信任判定（不含任何画面代码）。

信任判定的顺序（每一步都独立，任何一步不过就是“未核验”）：
1. 用磁盘上的实际字节重算 sha256；
2. 按哈希找核验结果文件，验 HMAC 签名（防记录被改）；
3. 签名钥匙必须在选择器自己的信任锚名单里；
4. 记录里的哈希必须等于第 1 步重算值（防换字节后沿用旧记录）；
5. 发行方身份以记录里的 publisher_id 为准，包元数据里的自称一律不采信。

包名、简介是制包者填写的 R 组文字，哪怕写着“已验证”也只是普通字，
绝不参与第 1-5 步，也不会出现在固定核验区的结论里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .digest import file_sha256
from .trust import load_public_anchors, verify


@dataclass(frozen=True)
class Verification:
    status: str                      # "verified" | "unverified" | "stale"
    publisher_id: str | None = None  # 只有 verified 才有发行方绑定
    publisher_display_name: str | None = None
    key_id: str | None = None
    verified_at: str | None = None
    reason: str = ""

    @property
    def label(self) -> str:
        return {
            "verified": "已验证",
            "unverified": "未核验",
            "stale": "记录失效",
        }[self.status]


@dataclass
class Entry:
    entry_id: str            # 目录内稳定标识：包文件名（不含元数据扩展名）
    package_path: Path
    meta_path: Path
    display_name: str        # 制包者填写，R 组，不可信
    summary: str             # 制包者填写，R 组，不可信
    claimed_publisher: str   # 制包者自称，仅作展示，不作身份
    sha256: str
    size: int
    verification: Verification

    @property
    def trusted(self) -> bool:
        return self.verification.status == "verified"


def _load_metadata(meta_path: Path) -> dict:
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    if raw.get("schema") != "package-meta/v1":
        raise ValueError(f"包元数据格式不对：{meta_path}")
    return raw


def _load_manifest(selector_dir: Path,
                   anchors: dict[str, bytes]) -> list[dict] | None:
    """读校验席的签名清单。验签失败一律按“没有清单”处理，
    清单只影响 stale/unverified 的提示措辞，不影响 verified 的硬判定。"""
    path = selector_dir / "attestations" / "manifest.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    mac = doc.get("manifest_signature")
    key_id = doc.get("signed_by_key_id")
    payload = {k: v for k, v in doc.items()
               if k not in ("manifest_signature", "signed_by_key_id")}
    if key_id not in anchors or not verify(payload, mac, anchors[key_id]):
        return None
    return payload.get("entries", [])


def _verify(package_path: Path, digest: str, selector_dir: Path,
            anchors: dict[str, bytes], manifest: list[dict] | None) -> Verification:
    att_dir = selector_dir / "attestations"
    att_path = att_dir / f"{digest}.json"

    if not att_path.exists():
        former = None
        if manifest is not None:
            former = [h for h in manifest if h["package_file"] == package_path.name]
        if former:
            old = former[-1]
            return Verification(
                status="stale",
                publisher_id=old.get("publisher_id"),
                publisher_display_name=old.get("publisher_display_name"),
                reason=(
                    f"旧核验绑定的是字节 {old['package_sha256'][:12]}…，"
                    f"当前字节是 {digest[:12]}…，哈希不一致，原核验不得沿用"
                ),
            )
        return Verification(status="unverified", reason="校验席没有这份字节的核验记录")

    doc = json.loads(att_path.read_text(encoding="utf-8"))
    mac = doc.pop("signature", None)
    key_id = doc.pop("signed_by_key_id", None)
    if mac is None or key_id is None:
        return Verification(status="unverified", reason="核验记录缺少签名字段")
    if key_id not in anchors:
        return Verification(status="unverified", reason="签名钥匙不在本选择器信任锚名单")
    if not verify(doc, mac, anchors[key_id]):
        return Verification(status="unverified", reason="核验记录签名不正确，记录可能被篡改")
    if doc.get("package_sha256") != digest:
        return Verification(
            status="stale",
            reason="记录哈希与磁盘字节不一致，字节已被更换",
        )
    if doc.get("package_file") and doc["package_file"] != package_path.name:
        return Verification(
            status="unverified",
            reason="核验记录绑定的包文件名与当前文件不符",
        )
    return Verification(
        status="verified",
        publisher_id=doc["publisher_id"],
        publisher_display_name=doc["publisher_display_name"],
        key_id=key_id,
        verified_at=doc["verified_at"],
        reason=doc.get("verifier_statement", ""),
    )


def load_entries(selector_dir: str | Path) -> list[Entry]:
    selector_dir = Path(selector_dir)
    anchors = load_public_anchors(selector_dir / "keyring.pub.json")
    manifest = _load_manifest(selector_dir, anchors)
    pkg_dir = selector_dir / "packages"
    entries: list[Entry] = []
    for meta_path in sorted(pkg_dir.glob("*.meta.json")):
        meta = _load_metadata(meta_path)
        package_path = pkg_dir / meta["package_file"]
        if not package_path.exists():
            continue
        digest = file_sha256(package_path)
        verification = _verify(package_path, digest, selector_dir, anchors, manifest)
        entries.append(
            Entry(
                entry_id=meta_path.name.removesuffix(".meta.json"),
                package_path=package_path,
                meta_path=meta_path,
                display_name=meta["display_name"],
                summary=meta["summary"],
                claimed_publisher=meta.get("claimed_publisher", "（未署名）"),
                sha256=digest,
                size=package_path.stat().st_size,
                verification=verification,
            )
        )
    order = _read_order(selector_dir / "order.conf", [e.entry_id for e in entries])
    rank = {eid: i for i, eid in enumerate(order)}
    entries.sort(key=lambda e: rank.get(e.entry_id, len(rank)))
    return entries


def _read_order(path: Path, fallback: list[str]) -> list[str]:
    if not path.exists():
        return fallback
    order = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    known = set(fallback)
    head = [eid for eid in order if eid in known]
    tail = [eid for eid in fallback if eid not in head]
    return head + tail


@dataclass
class Model:
    selector_dir: Path
    entries: list[Entry] = field(default_factory=list)
    cursor: int = 0

    @classmethod
    def open(cls, selector_dir: str | Path) -> "Model":
        selector_dir = Path(selector_dir)
        return cls(selector_dir=selector_dir, entries=load_entries(selector_dir))

    def reload(self) -> None:
        """字节被更换/还原后重新走一遍完整信任判定。"""
        keep = self.entries[self.cursor].entry_id if self.entries else None
        self.entries = load_entries(self.selector_dir)
        if keep is not None:
            ids = [e.entry_id for e in self.entries]
            if keep in ids:
                self.cursor = ids.index(keep)
        self.cursor = min(self.cursor, max(0, len(self.entries) - 1))

    def focus(self) -> Entry | None:
        if not self.entries:
            return None
        return self.entries[self.cursor]

    def move(self, delta: int) -> Entry | None:
        if not self.entries:
            return None
        self.cursor = (self.cursor + delta) % len(self.entries)
        return self.focus()

    def entry_by_id(self, entry_id: str) -> Entry | None:
        for e in self.entries:
            if e.entry_id == entry_id:
                return e
        return None
