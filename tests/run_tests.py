"""Dependency-free test runner (pytest is unavailable offline).

Installs a tiny ``pytest`` shim supplying the few features the suite uses
(``raises`` and ``mark.parametrize``), imports the test module, expands
parametrised cases, runs every ``test_*`` function and reports.

    python3 tests/run_tests.py
"""

import contextlib
import inspect
import os
import sys
import traceback
import types


def _install_pytest_shim():
    if "pytest" in sys.modules:
        return
    mod = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc):
            self.exc = exc

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"expected {self.exc.__name__}, none raised")
            if not issubclass(et, self.exc):
                return False
            return True

    class _Mark:
        def parametrize(self, argnames, argvalues):
            names = [n.strip() for n in argnames.split(",")]

            def deco(fn):
                cases = []
                for vals in argvalues:
                    if not isinstance(vals, (tuple, list)):
                        vals = (vals,)
                    cases.append(dict(zip(names, vals)))
                existing = getattr(fn, "_params", [])
                fn._params = existing + cases
                return fn

            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    mod.raises = lambda exc: _Raises(exc)
    mod.mark = _Mark()
    sys.modules["pytest"] = mod


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    sys.path.insert(0, root)
    sys.path.insert(0, here)
    _install_pytest_shim()

    import test_courier as suite

    tests = []
    for name, obj in sorted(vars(suite).items()):
        if name.startswith("test_") and callable(obj):
            params = getattr(obj, "_params", None)
            if params:
                for i, kwargs in enumerate(params):
                    tests.append((f"{name}[{i}]", obj, kwargs))
            else:
                tests.append((name, obj, {}))

    passed = failed = 0
    for name, fn, kwargs in tests:
        try:
            fn(**kwargs)
            passed += 1
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
