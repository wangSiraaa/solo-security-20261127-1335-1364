"""Stdlib-only test runner (no pytest available offline).

Discovers test_* functions in tests/test_*.py, injects the local pytest shim,
and reports pass/fail with tracebacks. Exit code is non-zero on any failure.
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
import tempfile
import traceback
import shutil


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    # provide the pytest shim before exec
    sys.modules.setdefault("pytest", __import__("pytest"))
    spec.loader.exec_module(mod)
    return mod


def _call(fn):
    """Call a test, supplying a fresh tmp_path when it asks for one."""
    params = inspect.signature(fn).parameters
    kwargs = {}
    tmp = None
    if "tmp_path" in params:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="t_"))
        kwargs["tmp_path"] = tmp
    try:
        fn(**kwargs)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv) -> int:
    sys.path.insert(0, str(pathlib.Path(__file__).parent))       # sealink
    sys.path.insert(0, str(pathlib.Path(__file__).parent / "_vendor"))  # pytest shim
    test_files = sorted(pathlib.Path(__file__).parent.joinpath("tests").glob("test_*.py"))
    if argv:
        test_files = [p for p in test_files if any(a in p.name for a in argv)]
    passed = failed = 0
    failures = []
    for tf in test_files:
        mod = _load(tf)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                _call(fn)
                passed += 1
                print(f"PASS {tf.name}::{name}")
            except Exception:
                failed += 1
                failures.append((tf.name, name, traceback.format_exc()))
                print(f"FAIL {tf.name}::{name}")
    if failures:
        print("\n" + "=" * 70)
        for f, n, tb in failures:
            print(f"\n--- {f}::{n} ---\n{tb}")
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
