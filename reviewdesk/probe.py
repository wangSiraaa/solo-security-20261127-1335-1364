from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .cabinet import read_events
from .hashing import sha256_file

_INSPECT = r"""
import importlib, inspect, json, hashlib
spec = %(spec)r
module_name, _, attr = spec.partition(':')
module = importlib.import_module(module_name)
fn = getattr(module, attr)
source_file = inspect.getsourcefile(fn)
module_file = inspect.getsourcefile(module)
try:
    source = inspect.getsource(fn)
except (OSError, TypeError):
    source = ''
print(json.dumps({
  'spec': spec,
  'module': module_name,
  'qualname': getattr(fn, '__qualname__', attr),
  'function_source_file': source_file,
  'module_source_file': module_file,
  'function_source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest() if source else None,
}))
"""


def inspect_entrypoint(root: str | Path, spec: str) -> dict[str, Any]:
    root = Path(root).resolve()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _INSPECT % {"spec": spec}],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return {"spec": spec, "ok": False, "stderr": proc.stderr.strip()}
    info = json.loads(proc.stdout)
    info["ok"] = True
    for key in ("function_source_file", "module_source_file"):
        path = info.get(key)
        if path and Path(path).exists():
            info[key + "_sha256"] = sha256_file(path)
            try:
                info[key + "_relative"] = str(Path(path).resolve().relative_to(root))
            except ValueError:
                info[key + "_relative"] = path
    return info


def run_lookup(
    root: str | Path,
    value: str,
    *,
    mode: str = "auto",
    offline: bool = True,
    entrypoint: str | None = None,
    wordlists: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    if offline:
        env["MOJIAO_OFFLINE"] = "1"
    if env_extra:
        env.update(env_extra)
    command = [sys.executable, "-m", "mojiao.cli", "--bundle-root", str(root), "lookup", value, "--mode", mode]
    if offline:
        command.append("--offline")
    if entrypoint:
        command.extend(["--entrypoint", entrypoint])
    for wordlist in wordlists or []:
        command.extend(["--wordlist", wordlist])
    proc = subprocess.run(command, cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result: dict[str, Any] = {
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    try:
        result["json"] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result["json"] = None
    return result


def audit_cabinets(state_paths: list[str | Path], before_counts: dict[str, int] | None = None) -> dict[str, Any]:
    cabinets = []
    total_new = 0
    for raw_path in state_paths:
        path = Path(raw_path)
        events = read_events(path)
        before = (before_counts or {}).get(str(path), 0)
        new_events = events[before:]
        total_new += len(new_events)
        cabinets.append(
            {
                "state_path": str(path),
                "total_events": len(events),
                "new_events": new_events,
                "new_count": len(new_events),
                "received_words": sorted({e.get("word", "") for e in new_events}),
            }
        )
    return {"total_new_events": total_new, "cabinets": cabinets, "no_egress_observed": total_new == 0}


def cabinet_counts(state_paths: list[str | Path]) -> dict[str, int]:
    return {str(Path(path)): len(read_events(path)) for path in state_paths}
