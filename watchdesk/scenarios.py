"""Built-in scenario runner.

The readable, editable command-level version is ``demo.sh``; this wrapper lets
the CLI expose the same acceptance walkthrough as ``python3 -m watchdesk scenario all``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_scenario(name: str, root: Path) -> str:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parent.parent
    script = project_root / "demo.sh"
    env = {**os.environ, "WATCHDECK_DEMO_ROOT": str(root)}

    if name == "all":
        subprocess.run(["bash", str(script)], check=True, cwd=project_root, env=env)
        return f"全部场景完成，证据目录：{root}"

    # The shell walkthrough is deliberately small and idempotent; run it then
    # point at the specific generated report.
    subprocess.run(["bash", str(script)], check=True, cwd=project_root, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mapping = {
        "emergency-matrix": "emergency-inside-carry-pair/report.txt",
        "success": "success/report.txt",
        "missing-middle": "missing-middle",
        "revoke-delivery": "revoke-delivery/report.txt",
        "version-switch": "version-switch",
    }
    target = mapping.get(name, "")
    path = (root / target).resolve()
    return f"场景 {name} 已生成：{path}"


if __name__ == "__main__":
    print(run_scenario("all", Path("/tmp/watchdesk-scenarios")))
    sys.exit(0)
