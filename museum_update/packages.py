"""Publisher package verification."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .errors import VerificationError
from .security import canonical_json, set_digest, sha256_file


@dataclass(frozen=True)
class VerifiedPackage:
    root: Path
    order_id: str
    generation: int
    payload_root: Path
    file_digests: Dict[str, str]
    components: List[Dict[str, Any]]
    set_digest: str
    manifest: Dict[str, Any]
    approval: Dict[str, Any]


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"缺少文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"JSON 无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"顶层结构必须是对象: {path}")
    return value


def _safe_rel_path(rel_path: Any, payload_root: Path) -> str:
    if not isinstance(rel_path, str) or not rel_path:
        raise VerificationError(f"文件路径必须是非空字符串: {rel_path!r}")
    if "\\" in rel_path or rel_path.startswith("/") or "\x00" in rel_path:
        raise VerificationError(f"不安全的文件路径: {rel_path!r}")
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise VerificationError(f"不安全的文件路径: {rel_path!r}")
    root = payload_root.resolve()
    resolved = (root / rel_path).resolve()
    if not resolved.is_relative_to(root):
        raise VerificationError(f"文件路径越出负载目录: {rel_path!r}")
    return rel_path


def verify_package(root: str | Path) -> VerifiedPackage:
    root = Path(root).resolve()
    if not root.is_dir():
        raise VerificationError(f"发布包目录不存在: {root}")

    approval_path = root / "approval.json"
    manifest_path = root / "manifest.json"
    approval = _load_json(approval_path)
    manifest = _load_json(manifest_path)

    if approval.get("schema") != "museum-update-approval/v1":
        raise VerificationError("approval.json schema 不受支持")
    if manifest.get("schema") != "museum-update-manifest/v1":
        raise VerificationError("manifest.json schema 不受支持")

    order_id = str(manifest.get("order_id", ""))
    generation = manifest.get("generation")
    if not order_id:
        raise VerificationError("manifest 缺少 order_id")
    if not isinstance(generation, int) or generation <= 0:
        raise VerificationError("manifest generation 必须是正整数")
    if approval.get("order_id") != order_id:
        raise VerificationError("批准书 order_id 与清单不一致")
    if approval.get("generation") != generation:
        raise VerificationError("批准书代次与清单不一致")

    actual_manifest_digest = sha256_file(manifest_path)
    if approval.get("manifest_digest") != actual_manifest_digest:
        raise VerificationError(
            "manifest.json 摘要不是发布方获准摘要: "
            f"expected={approval.get('manifest_digest')} actual={actual_manifest_digest}"
        )

    payload_name = manifest.get("payload_root", "files")
    if not isinstance(payload_name, str) or "/" in payload_name or payload_name in ("", ".", ".."):
        raise VerificationError("payload_root 必须是包内单层目录名")
    payload_root = root / payload_name
    if not payload_root.is_dir():
        raise VerificationError(f"负载目录不存在: {payload_root}")

    listed_files = manifest.get("files")
    approved_files = approval.get("file_digests")
    if not isinstance(listed_files, dict) or not listed_files:
        raise VerificationError("manifest.files 必须是非空 路径->摘要 对象")
    listed_files = {_safe_rel_path(path, payload_root): digest for path, digest in listed_files.items()}
    if not isinstance(approved_files, dict) or not approved_files:
        raise VerificationError("approval.file_digests 必须是非空 路径->获准摘要 对象")
    if listed_files != approved_files:
        raise VerificationError("清单中的文件摘要集合与批准摘要不一致")

    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise VerificationError("manifest.components 必须是非空配套组件清单")
    if approval.get("components") != components:
        raise VerificationError("配套组件清单与批准的组件清单不一致")

    component_paths = set()
    for component in components:
        if not isinstance(component, dict):
            raise VerificationError("每个 component 必须是对象")
        path = _safe_rel_path(component.get("path"), payload_root)
        name = component.get("name")
        if not isinstance(name, str) or not name:
            raise VerificationError(f"组件 {path!r} 缺少 name")
        if path not in listed_files:
            raise VerificationError(f"组件 {name} 未出现在获准文件摘要表中")
        component_paths.add(path)

    actual_files = set()
    for path in payload_root.rglob("*"):
        if path.is_symlink():
            raise VerificationError(f"负载不允许符号链接: {path}")
        if path.is_file():
            rel = str(path.relative_to(payload_root)).replace("\\", "/")
            _safe_rel_path(rel, payload_root)
            actual_files.add(rel)
    listed_paths = set(listed_files)
    if actual_files != listed_paths:
        missing = sorted(listed_paths - actual_files)
        extra = sorted(actual_files - listed_paths)
        raise VerificationError(f"实际文件集合不是获准的一套; 缺少={missing}, 未获准额外文件={extra}")

    actual_digests: Dict[str, str] = {}
    for rel_path, expected_digest in listed_files.items():
        file_path = payload_root / rel_path
        if not file_path.is_file():
            raise VerificationError(f"缺少负载文件: {rel_path}")
        actual_digest = sha256_file(file_path)
        if actual_digest != expected_digest:
            raise VerificationError(
                f"配套字节未获准: {rel_path}: expected={expected_digest}, actual={actual_digest}"
            )
        actual_digests[rel_path] = actual_digest

    if approval.get("approved_set_digest") != set_digest(actual_digests):
        raise VerificationError("整套摘要与批准的 approved_set_digest 不一致")

    expected_component_set = approval.get("component_set_digest")
    component_set = [
        {
            "name": c["name"],
            "path": c["path"],
            "role": c.get("role", "companion"),
            "digest": actual_digests[c["path"]],
        }
        for c in components
    ]
    if expected_component_set is not None:
        from .security import sha256_bytes

        actual_component_set_digest = sha256_bytes(canonical_json(component_set))
        if expected_component_set != actual_component_set_digest:
            raise VerificationError("配套组件集合摘要未获准")

    return VerifiedPackage(
        root=root,
        order_id=order_id,
        generation=generation,
        payload_root=payload_root,
        file_digests=actual_digests,
        components=components,
        set_digest=set_digest(actual_digests),
        manifest=manifest,
        approval=approval,
    )
