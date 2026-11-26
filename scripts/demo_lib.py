from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent


class Recorder:
    def __init__(self, title: str, intro: str = "") -> None:
        self.title = title
        self.intro = intro
        self.blocks: list[str] = []
        self.checks: list[str] = []

    def run(
        self,
        args: Iterable[str],
        *,
        state_dir: Path,
        expect: int = 0,
        note: str = "",
        record: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        args = [str(a) for a in args]
        cmd = [
            sys.executable,
            "-m",
            "museum_update",
            "--state-dir",
            str(state_dir),
            *args,
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
        )
        if proc.returncode != expect:
            raise AssertionError(
                f"command expected {expect}, got {proc.returncode}: {' '.join(cmd)}\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        if record:
            shown = ["python3", "-m", "museum_update", "--state-dir", "<state>", *args]
            output = proc.stdout
            if proc.stderr:
                output += ("\n" if output and not output.endswith("\n") else "") + proc.stderr
            heading = f"### {note}\n\n" if note else ""
            self.blocks.append(
                f"{heading}`$ {' '.join(shown)}`\n\n```text\n{output.rstrip()}\n```\n"
            )
        return proc

    def check(self, description: str, ok: bool, detail: str = "") -> None:
        if not ok:
            raise AssertionError(description + (f": {detail}" if detail else ""))
        self.checks.append(description)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        parts = [f"# {self.title}\n"]
        if self.intro:
            parts.append(self.intro + "\n")
        if self.checks:
            parts.append("## 自动核对点\n")
            parts.extend(f"- ✅ {item}" for item in self.checks)
            parts.append("")
        parts.append("## 实际命令与输出\n")
        parts.extend(self.blocks)
        path.write_text("\n".join(parts), encoding="utf-8")
