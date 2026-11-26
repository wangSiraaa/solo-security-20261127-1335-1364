"""Minimal stdlib test runner (no pytest available in this environment).

Supports exactly the subset the test-suite uses:

* discovery of module-level ``test_*`` functions in ``tests/test_*.py``
* ``with pytest.raises(Exc):`` (and ``match=`` regex)
* a stand-in ``pytest`` module shim

Exit code is non-zero on the first failing module, after reporting all.
Run with::

    python3 run_tests.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
import traceback
import types
from pathlib import Path


# ---------------------------------------------------------------------------
# pytest shim -- installed into sys.modules before tests import it.
# ---------------------------------------------------------------------------
def _make_pytest_shim() -> types.ModuleType:
    mod = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc, match=None):
            self.exc = exc
            self.match = match
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise AssertionError(f"DID NOT RAISE {self.exc}")
            if not issubclass(exc_type, self.exc):
                return False  # let the wrong exception propagate
            self.value = exc
            if self.match is not None and not re.search(self.match, str(exc)):
                raise AssertionError(
                    f"exception {exc!r} did not match {self.match!r}"
                )
            return True  # swallow the expected exception

    def raises(exc, match=None):
        return _Raises(exc, match)

    def fail(msg=""):
        raise AssertionError(msg)

    mod.raises = raises
    mod.fail = fail
    return mod


sys.modules.setdefault("pytest", _make_pytest_shim())


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def discover(root: Path):
    for path in sorted(root.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(f"tests.{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield path, module


def main() -> int:
    root = Path(__file__).parent
    tests_dir = root / "tests"
    sys.path.insert(0, str(root))
    if not tests_dir.is_dir():
        print("no tests/ directory", file=sys.stderr)
        return 2

    total = passed = failed = 0
    failures = []
    for path, module in discover(tests_dir):
        names = [n for n in dir(module) if n.startswith("test_")]
        for name in names:
            total += 1
            fn = getattr(module, name)
            try:
                fn()
            except Exception:
                failed += 1
                failures.append((path.name, name, traceback.format_exc()))
                print(f"FAIL {path.name}::{name}")
            else:
                passed += 1
                print(f"ok   {path.name}::{name}")

    print()
    if failures:
        for fname, name, tb in failures:
            print("=" * 70)
            print(f"{fname}::{name}")
            print(tb)
    print(f"{passed}/{total} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
