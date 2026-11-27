"""无 TTY 的脚本模式：供自动化验收与“读屏”核对使用。

输出的朗读稿带机器可解析的稳定前缀：
  SPEAK n     第 n 项的条目句与结论句
  FOCUS ...   聚焦详情
安装动作同样走 installer 的完整重验流程，拒绝时以非零码退出。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .installer import InstallError, install
from .model import Model
from .renderer import render


@dataclass
class ScriptResult:
    frame: object
    action: str
    detail: dict


def show(model: Model, width: int, height: int = 24) -> ScriptResult:
    frame = render(model, width, height)
    return ScriptResult(frame=frame, action="show", detail={})


def select_and_install(model: Model, entry_id: str, width: int,
                       install_root: str | Path, height: int = 24) -> ScriptResult:
    target = model.entry_by_id(entry_id)
    if target is None:
        raise InstallError(f"条目不存在：{entry_id}")
    model.cursor = [e.entry_id for e in model.entries].index(entry_id)
    frame = render(model, width, height)
    result = install(model, entry_id, install_root, frame)
    return ScriptResult(
        frame=frame,
        action="installed",
        detail={
            "entry_id": result.entry_id,
            "receipt": str(result.receipt_path),
            "sha256": result.sha256,
            "installed_path": str(result.installed_path),
        },
    )


def parse_speech_verdicts(speech_text: str) -> list[dict]:
    """从朗读稿里抽取列表概览中的“校验席结论”句，供测试依据朗读而非行号做选择。

    聚焦详情里也有以“校验席结论”开头的句子，但不含状态码/依据哈希，
    这里用字段齐备性把两类句子区分开。
    """
    out = []
    item_index = None
    for line in speech_text.splitlines():
        if line.startswith("第 "):
            # “第 n 项[，当前聚焦]：…”
            item_index = int(line.split(" ", 2)[1])
        if not line.startswith("校验席结论："):
            continue
        if "状态码 " not in line or "依据哈希 " not in line:
            continue  # 聚焦详情句，跳过
        verdict = line.removeprefix("校验席结论：").split("，", 1)[0]
        status_code = line.split("状态码 ", 1)[1].split("；", 1)[0]
        basis_hash = line.split("依据哈希 ", 1)[1].rstrip("。")
        out.append({
            "item_index": item_index,
            "verdict": verdict,
            "status": status_code,
            "basis_hash": basis_hash,
            "raw": line,
        })
    return out


def dump_json(result: ScriptResult) -> str:
    return json.dumps({
        "action": result.action,
        "detail": result.detail,
        "speech": result.frame.speech_lines,
        "visual": result.frame.visual_lines,
    }, ensure_ascii=False, indent=2)
