from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .analysis import AdmissionReport, analyze_package
from .canvas import CanvasReadback
from .canonical import canonical_bytes, canonical_hash, no_duplicate_object_keys
from .conditions import ConditionContext
from .engine import BUILTIN_FALLBACK_ID, BUILTIN_FALLBACK_OPS, execute_declaration
from .errors import StaleAdmissionError, StudioError
from .package import Package, load_package_file, parse_package_data
from .profiles import ClassicPolicy, ImportLimits, LayoutProfile, MachinePolicy


class FontStudio:
    """Import packages, issue admissions, lock layouts, and read canvases."""

    def __init__(
        self,
        store_dir: str | Path | None = None,
        *,
        import_limits: ImportLimits | None = None,
        classic_policy: ClassicPolicy | None = None,
        machine_policy: MachinePolicy | None = None,
    ):
        self.store_dir = Path(store_dir) if store_dir is not None else None
        self.limits = import_limits or ImportLimits()
        self.classic_policy = classic_policy or ClassicPolicy()
        self.default_machine_policy = machine_policy or MachinePolicy()
        self._loaded: dict[str, Package] = {}
        self._admissions: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, dict[str, Any]] = {}
        self._index: dict[str, dict[str, str]] = {}

        if self.store_dir is not None:
            (self.store_dir / "packages").mkdir(parents=True, exist_ok=True)
            (self.store_dir / "admissions").mkdir(parents=True, exist_ok=True)
            (self.store_dir / "locks").mkdir(parents=True, exist_ok=True)
            self._load_index()

    # ---------- storage helpers ----------

    def _index_path(self) -> Path:
        assert self.store_dir is not None
        return self.store_dir / "index.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if path.exists():
            self._index = json.loads(path.read_text(encoding="utf-8"))

    def _save_index(self) -> None:
        if self.store_dir is None:
            return
        tmp = self._index_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._index_path())

    # ---------- import / admission ----------

    def _accept_raw(self, raw: Any, source_bytes: int) -> dict[str, Any]:
        package = parse_package_data(raw, source_bytes=source_bytes, limits=self.limits)
        report = analyze_package(package, self.classic_policy)
        report_dict = report.to_dict()

        self._loaded[package.content_hash] = package
        self._admissions[report.admission_id] = report_dict
        self._index[package.package_id] = {
            "content_hash": package.content_hash,
            "admission_id": report.admission_id,
        }

        if self.store_dir is not None:
            pkg_path = self.store_dir / "packages" / f"{package.content_hash}.json"
            if not pkg_path.exists():
                pkg_path.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            adm_path = self.store_dir / "admissions" / f"{report.admission_id}.json"
            adm_path.write_text(
                json.dumps(report_dict, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._save_index()

        return self.import_receipt(package, report)

    def import_package(self, source: "str | Path | dict[str, Any]") -> dict[str, Any]:
        """Convenience importer accepting a dict, JSON text, or filesystem path."""

        if isinstance(source, dict):
            return self.import_dict(source)
        if isinstance(source, Path):
            return self.import_path(source)
        if isinstance(source, str):
            stripped = source.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                return self.import_json_text(source)
            return self.import_path(source)
        raise TypeError("source must be a package dict, JSON string, or path")

    def import_dict(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError("import_dict expects a JSON object/dict")
        source_bytes = len(canonical_bytes(raw))
        return self._accept_raw(raw, source_bytes)

    def import_json_text(self, text: str) -> dict[str, Any]:
        source_bytes = len(text.encode("utf-8"))
        if source_bytes > self.limits.max_package_bytes:
            from .errors import PackageQuotaError

            raise PackageQuotaError(
                f"package is {source_bytes} bytes; import quota is {self.limits.max_package_bytes}"
            )
        try:
            raw = json.loads(text, object_pairs_hook=no_duplicate_object_keys)
        except json.JSONDecodeError as exc:
            from .errors import PackageFormatError

            raise PackageFormatError(f"invalid JSON package: {exc}") from exc
        return self._accept_raw(raw, source_bytes)

    def import_path(self, path: str | Path) -> dict[str, Any]:
        p = Path(path)
        package = load_package_file(p, self.limits)
        data = p.read_bytes()  # load_package_file already enforced the byte quota
        report = analyze_package(package, self.classic_policy)
        report_dict = report.to_dict()
        self._loaded[package.content_hash] = package
        self._admissions[report.admission_id] = report_dict
        self._index[package.package_id] = {
            "content_hash": package.content_hash,
            "admission_id": report.admission_id,
        }
        if self.store_dir is not None:
            pkg_path = self.store_dir / "packages" / f"{package.content_hash}.json"
            if not pkg_path.exists():
                pkg_path.write_bytes(data)
            adm_path = self.store_dir / "admissions" / f"{report.admission_id}.json"
            adm_path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            self._save_index()
        return self.import_receipt(package, report)

    def import_receipt(self, package: Package, report: AdmissionReport) -> dict[str, Any]:
        current = self._index.get(package.package_id, {})
        return {
            "package_id": package.package_id,
            "name": package.name,
            "version": package.version,
            "content_hash": package.content_hash,
            "source_bytes": package.source_bytes,
            "admission_id": report.admission_id,
            "current_revision": current.get("content_hash") == package.content_hash,
            "classic_glyphs": report.classic_glyphs,
            "classic_glyph_count": len(report.classic_glyphs),
            "glyph_count": len(package.glyphs),
            "primitive_count": len(package.primitives),
            "rejected": report.rejected,
            "cyclic_primitives": report.cyclic_primitives,
            "fallback_char": package.fallback_char,
            "fallback_admitted": report.fallback_admitted,
            "limits": self.limits.__dict__,
            "classic_policy": self.classic_policy.__dict__,
        }

    def list_packages(self) -> list[dict[str, str]]:
        return [
            {"package_id": pid, "content_hash": meta["content_hash"], "admission_id": meta["admission_id"]}
            for pid, meta in sorted(self._index.items())
        ]

    def _load_package_hash(self, content_hash: str) -> Package:
        if content_hash in self._loaded:
            return self._loaded[content_hash]
        if self.store_dir is None:
            raise StudioError(f"package hash {content_hash} is not loaded")
        path = self.store_dir / "packages" / f"{content_hash}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        package = parse_package_data(
            raw,
            source_bytes=path.stat().st_size,
            limits=self.limits,
        )
        self._loaded[content_hash] = package
        return package

    def get_package(self, package_id: str, content_hash: str | None = None) -> tuple[Package, AdmissionReport]:
        if content_hash is None:
            meta = self._index.get(package_id)
            if meta is None:
                raise StudioError(f"no imported package with id {package_id!r}; have {self.list_packages()}")
            content_hash = meta["content_hash"]
        package = self._load_package_hash(content_hash)
        report = analyze_package(package, self.classic_policy)
        return package, report

    def analyze(self, package_id: str) -> dict[str, Any]:
        """Return dependencies, cycles, and static expansion upper bounds."""

        _, report = self.get_package(package_id)
        return report.to_dict()

    def verify_admission(self, admission_id: str) -> dict[str, Any]:
        record = self._admissions.get(admission_id)
        if record is None and self.store_dir is not None:
            path = self.store_dir / "admissions" / f"{admission_id}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                self._admissions[admission_id] = record
        if record is None:
            raise StudioError(f"unknown admission {admission_id!r}")

        package_id = record["package_id"]
        current_hash = self._index.get(package_id, {}).get("content_hash")
        if current_hash != record["content_hash"]:
            raise StaleAdmissionError(admission_id, package_id, current_hash)
        return {
            "valid": True,
            "admission_id": admission_id,
            "package_id": package_id,
            "content_hash": record["content_hash"],
            "classic_glyphs": record["classic_glyphs"],
        }

    def classic_cut(
        self,
        package_id: str,
        *,
        chars: list[str] | None = None,
        out_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Analyze dependencies and cut an old-machine whole-glyph font pack."""

        from .cutter import make_classic_cut, write_classic_cut

        package, report = self.get_package(package_id)
        if out_path is not None:
            return write_classic_cut(out_path, package, report, chars=chars)
        return make_classic_cut(package, report, chars=chars)

    # ---------- layout-time promises ----------

    def promised_chars(
        self,
        package_id: str,
        *,
        font_size: int = 5,
        line_width: int = 16,
        mode: str = "live",
        policy: MachinePolicy | None = None,
    ) -> dict[str, Any]:
        package, admission = self.get_package(package_id)
        machine = policy or self.default_machine_policy
        machine.validate()
        profile = LayoutProfile(text="", font_size=font_size, line_width=line_width, mode=mode)

        if mode == "classic":
            return {
                "package_id": package_id,
                "profile": profile.to_dict(),
                "promised_chars": admission.classic_glyphs,
                "source": "install_time_static_upper_bound",
            }

        context = ConditionContext(profile)

        def resolver(pid: str):
            if pid == BUILTIN_FALLBACK_ID:
                return BUILTIN_FALLBACK_OPS
            return package.primitives[pid]

        promised: list[str] = []
        details: dict[str, Any] = {}
        for char, glyph in package.glyphs.items():
            result = execute_declaration(
                glyph.root,
                package.primitives[glyph.root],
                resolver,
                context,
                machine,
                0,
                0,
            )
            details[char] = result.to_dict()
            if result.ok:
                promised.append(char)
        return {
            "package_id": package_id,
            "profile": profile.to_dict()
            | {
                "canvas_width": machine.canvas_width,
                "canvas_height": machine.canvas_height,
                "work_budget": machine.work_budget,
                "cell_budget": machine.cell_budget,
            },
            "promised_chars": promised,
            "source": "layout_time_actual_sandbox_execution",
            "details": details,
            "condition_decisions": context.decision_records(),
        }

    # ---------- locked composition ----------

    def lock_layout(
        self,
        text: str | LayoutProfile,
        *,
        package_id: str | None = None,
        font_size: int = 5,
        line_width: int = 16,
        mode: str = "live",
        canvas_width: int | None = None,
        canvas_height: int | None = None,
        work_budget: int | None = None,
        cell_budget: int | None = None,
        max_ref_depth: int | None = None,
        lock_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(text, LayoutProfile):
            profile = text
            if package_id is None:
                raise ValueError("package_id is required when passing a LayoutProfile")
        else:
            profile = LayoutProfile(
                text=text,
                font_size=font_size,
                line_width=line_width,
                mode=mode,
            )
        assert package_id is not None

        package, admission = self.get_package(package_id)
        base = self.default_machine_policy
        if profile.mode == "classic":
            # The old machine has no preemptible runner; install accounting is
            # the guarantee.  Give the deterministic simulator the same fixed
            # capacity that ClassicPolicy used for its upper bound.
            default_work_budget = self.classic_policy.max_work
            default_cell_budget = self.classic_policy.max_cell_writes
        else:
            default_work_budget = base.work_budget
            default_cell_budget = base.cell_budget
        machine = MachinePolicy(
            canvas_width=canvas_width if canvas_width is not None else base.canvas_width,
            canvas_height=canvas_height if canvas_height is not None else base.canvas_height,
            line_height=base.line_height,
            work_budget=work_budget if work_budget is not None else default_work_budget,
            cell_budget=cell_budget if cell_budget is not None else default_cell_budget,
            max_ref_depth=max_ref_depth if max_ref_depth is not None else base.max_ref_depth,
        )
        machine.validate()

        from .engine import LayoutEngine

        engine = LayoutEngine(package, admission, machine)
        if lock_id is None:
            lock_id = "lock_" + canonical_hash(
                {
                    "package_hash": package.content_hash,
                    "admission_id": admission.admission_id,
                    "text": profile.text,
                    "font_size": profile.font_size,
                    "line_width": profile.line_width,
                    "mode": profile.mode,
                    "machine": machine.__dict__,
                }
            )[:20]

        lock = engine.lock(profile, lock_id)

        existing: dict[str, Any] | None = self._locks.get(lock_id)
        lock_path: Path | None = None
        if existing is None and self.store_dir is not None:
            lock_path = self.store_dir / "locks" / f"{lock_id}.json"
            if lock_path.exists():
                existing = json.loads(lock_path.read_text(encoding="utf-8"))

        if existing is not None:
            if canonical_hash(existing) != canonical_hash(lock):
                raise StudioError(f"lock id {lock_id!r} is already sealed by a different layout")
            # Identical deterministic request: keep the first sealed snapshot.
            return existing

        self._locks[lock_id] = lock
        if self.store_dir is not None:
            if lock_path is None:
                lock_path = self.store_dir / "locks" / f"{lock_id}.json"
            lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return lock

    # Vocabulary alias for acceptance code that says "lock typesetting".
    lock_typesetting = lock_layout

    def get_lock(self, lock_id_or_path: str) -> dict[str, Any]:
        if lock_id_or_path in self._locks:
            return self._locks[lock_id_or_path]
        p = Path(lock_id_or_path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        if self.store_dir is not None:
            path = self.store_dir / "locks" / f"{lock_id_or_path}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise StudioError(f"unknown lock {lock_id_or_path!r}")

    def read_canvas(self, lock_id_or_path: str) -> CanvasReadback:
        lock = self.get_lock(lock_id_or_path)
        c = lock["canvas"]
        return CanvasReadback(
            lock_id=lock["lock_id"],
            width=c["width"],
            height=c["height"],
            matrix=c["matrix"],
            ascii_art=c["ascii"],
            unicode_art=c["unicode"],
            filled_cells=c["filled_cells"],
        )

    read_virtual_canvas = read_canvas
    read_back_canvas = read_canvas
