"""Minimal pytest shim: only `raises` + a collector, stdlib only."""
import contextlib
import traceback


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        if not issubclass(et, self.exc):
            return False
        self.value = ev
        return True


def raises(exc):
    return _Raises(exc)
