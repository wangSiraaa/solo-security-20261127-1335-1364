"""校验席：在自己的受信目录里核验“实际收到的包”。

输入（受信目录 trustdir）：
  keyring.json          本席私钥环（首次自动生成）
  keyring.pub.json      导出的信任锚，交给选择器目录
  trust.json            受信发行方名册：publisher_id -> 展示名（本席登记）
  inbox/<publisher>/    本席实际收到的包，每个发行方一个子目录

输出（同样写在受信目录，再由外部步骤搬运到选择器目录）：
  attestations/<sha256>.json   一份核验结果只绑定 字节哈希 + 发行方
  attestations/manifest.json   签名清单：本席历次核验过的 文件名->哈希 历史

清单同样经 HMAC 签名，但它只用于把“从未核验”和“核验过但字节已换（失效）”
区分开；两种状态在选择器里都不是已验证、都阻止安装，清单被篡改最多影响
提示措辞，不可能给任何字节背书。

核验结果不绑定包名：两家发行方可以起完全相同的名字，名字是制包者填的
普通字，不参与身份判断。绑定的是 publisher_id（本席名册里的）与字节哈希。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .digest import canonical, file_sha256
from .trust import Keyring, sign


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Attestation:
    payload: dict
    signature: str
    signed_by: str

    def to_json(self) -> str:
        body = dict(self.payload)
        body["signature"] = self.signature
        body["signed_by_key_id"] = self.signed_by
        return json.dumps(body, ensure_ascii=False, indent=2)


def _load_trust_roster(trust_path: Path) -> dict[str, str]:
    if not trust_path.exists():
        raise FileNotFoundError(
            f"受信发行方名册不存在：{trust_path}；校验席不能凭空信任发行方"
        )
    raw = json.loads(trust_path.read_text(encoding="utf-8"))
    if raw.get("kind") != "verifier-trusted-publishers":
        raise ValueError("名册类型不对")
    return {item["publisher_id"]: item["display_name"] for item in raw["publishers"]}


def run(trustdir: str | Path) -> list[Attestation]:
    trustdir = Path(trustdir)
    keyring_path = trustdir / "keyring.json"
    pub_path = trustdir / "keyring.pub.json"
    if keyring_path.exists():
        keyring = Keyring.load(keyring_path)
    else:
        keyring = Keyring.generate()
        keyring.save(keyring_path)
    keyring.export_public(pub_path)

    roster = _load_trust_roster(trustdir / "trust.json")
    inbox = trustdir / "inbox"
    out_dir = trustdir / "attestations"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 清单是累积的：同一文件名换了字节再送检，新旧哈希都会留下，
    # 选择器据此判断“旧记录存在但与当前字节不符”。
    manifest_path = out_dir / "manifest.json"
    history: list[dict] = []
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        history = old.get("entries", [])

    results: list[Attestation] = []
    for publisher_id in sorted(p.name for p in inbox.iterdir() if p.is_dir()):
        if publisher_id not in roster:
            # 不在名册里的发行方：本席不签发，选择器侧自然显示未核验。
            continue
        for pkg_path in sorted(inbox.joinpath(publisher_id).iterdir()):
            # 只核验实际字节包；.meta.json 是制包者填写的附属材料，
            # 不产生任何核验记录。
            if not pkg_path.is_file() or pkg_path.suffix != ".pkg":
                continue
            digest = file_sha256(pkg_path)
            payload = {
                "schema": "attestation/v1",
                "publisher_id": publisher_id,
                "publisher_display_name": roster[publisher_id],
                "package_file": pkg_path.name,
                "package_sha256": digest,
                "package_size": pkg_path.stat().st_size,
                "verified_at": _utc_now(),
                "verifier_statement": "字节与发行方身份已由校验席核验绑定",
            }
            key_id = keyring.key_id
            mac = sign(payload, keyring.secret(key_id))
            att = Attestation(payload=payload, signature=mac, signed_by=key_id)
            (out_dir / f"{digest}.json").write_text(att.to_json(), encoding="utf-8")
            results.append(att)
            history = [h for h in history
                       if not (h["package_file"] == pkg_path.name
                               and h["package_sha256"] == digest)]
            history.append({
                "package_file": pkg_path.name,
                "package_sha256": digest,
                "publisher_id": publisher_id,
                "publisher_display_name": roster[publisher_id],
                "verified_at": payload["verified_at"],
            })

    manifest_payload = {
        "schema": "attestation-manifest/v1",
        "generated_at": _utc_now(),
        "entries": sorted(history, key=lambda h: (h["package_file"], h["verified_at"])),
    }
    key_id = keyring.key_id
    manifest_doc = dict(manifest_payload)
    manifest_doc["manifest_signature"] = sign(
        manifest_payload, keyring.secret(key_id)
    )
    manifest_doc["signed_by_key_id"] = key_id
    manifest_path.write_text(
        json.dumps(manifest_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def attestation_path(trustdir: str | Path, digest: str) -> Path:
    return Path(trustdir) / "attestations" / f"{digest}.json"


def write_roster(trustdir: str | Path, publishers: list[tuple[str, str]]) -> None:
    body = {
        "kind": "verifier-trusted-publishers",
        "publishers": [
            {"publisher_id": pid, "display_name": name}
            for pid, name in sorted(publishers)
        ],
    }
    Path(trustdir, "trust.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = ["run", "attestation_path", "write_roster", "Attestation", "canonical"]
