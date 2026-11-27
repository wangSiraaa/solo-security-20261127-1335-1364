from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

from .hashing import canonical_json, sha256_file
from .ledger import utc_now
from .seals import PUBLIC_KEY, sign_bytes

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def _ignore_pycache(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def _copy_path(source_root: Path, rel: str, dest_root: Path) -> None:
    source = source_root / rel
    if not source.exists():
        return
    target = dest_root / rel
    if source.is_dir():
        shutil.copytree(source, target, ignore=_ignore_pycache, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_bundle(
    *,
    source_root: str | Path,
    bundle_id: str,
    classroom: dict[str, Any],
    endorsements: list[dict[str, Any]] | None = None,
    endorsement_sources: list[str | Path] | None = None,
    include_wordlists: list[str] | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    bundle_dir = DIST / bundle_id
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    _copy_path(source_root, "mojiao", bundle_dir)
    _copy_path(source_root, "contracts", bundle_dir)
    if include_wordlists is None:
        _copy_path(source_root, "wordlists", bundle_dir)
    else:
        for rel in include_wordlists:
            _copy_path(source_root, rel, bundle_dir)

    assets = ROOT / "bundle-assets"
    shutil.copy2(assets / "verify.py", bundle_dir / "verify.py")
    (bundle_dir / "verify.py").chmod(0o755)
    ww = bundle_dir / "ww"
    shutil.copy2(assets / "ww", ww)
    ww.chmod(0o755)

    (bundle_dir / "classroom.json").write_text(
        json.dumps(classroom, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    endorsement_files: list[str] = []
    end_dir = bundle_dir / "endorsements"
    end_dir.mkdir(exist_ok=True)
    for doc in endorsements or []:
        rel = f"endorsements/{doc['endorsement_id']}.json"
        (bundle_dir / rel).write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        endorsement_files.append(rel)
    for source in endorsement_sources or []:
        source = Path(source)
        rel = f"endorsements/{source.name}"
        shutil.copy2(source, bundle_dir / rel)
        endorsement_files.append(rel)

    shutil.copy2(PUBLIC_KEY, bundle_dir / "PUBLISHER.pub")

    files: dict[str, str] = {}
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json" or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        files[rel] = sha256_file(path)

    manifest_payload = {
        "bundle_id": bundle_id,
        "created_at": utc_now(),
        "classroom_id": classroom.get("classroom_id"),
        "entrypoint": classroom.get("entrypoint"),
        "files": files,
        "endorsement_files": sorted(set(endorsement_files)),
    }
    manifest = dict(manifest_payload)
    manifest["key_id"] = "fictional-publisher-ed25519-2026Q4"
    manifest["signature_algorithm"] = "Ed25519"
    manifest["signature"] = sign_bytes(canonical_json(manifest))
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive = DIST / f"{bundle_id}.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle_dir, arcname=bundle_id)

    return {
        "bundle_id": bundle_id,
        "directory": str(bundle_dir),
        "archive": str(archive),
        "archive_sha256": sha256_file(archive),
        "manifest_sha256": sha256_file(bundle_dir / "MANIFEST.json"),
        "file_count": len(files),
    }
