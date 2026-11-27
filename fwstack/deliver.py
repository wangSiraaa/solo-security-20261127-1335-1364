"""交付配置：装板员可在本地解释器回放的自描述 JSON。

配置携带程序与板卡源文件的 SHA-256 指纹；只移动开放中断点或改动保留区后，
旧配置立即失效，必须用 analyze 重算峰值/响应后重新交付。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict

from .analyzer import AnalysisResult
from .model import ISOLATED, SHARED, Board, Program


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_config(result: AnalysisResult, stack_model: str,
                 arrivals: dict[str, int] | None = None) -> dict:
    if stack_model not in (SHARED, ISOLATED):
        raise ValueError(f"未知栈接法 {stack_model!r}")
    board = result.board
    if board is None:
        raise ValueError("交付配置必须绑定一块板卡")
    plan = result.plans.get(stack_model)
    if plan is None or not plan.fits:
        raise ValueError(
            f"栈接法 {stack_model} 在板卡 {board.name} 上放不进，无法交付；"
            "未获保证的组合见分析报告的缺口列表"
        )
    prog = result.program
    arrivals = arrivals or {lvl: prog.isrs[lvl].arrivals_per_period for lvl in prog.isrs}
    for lvl, n in arrivals.items():
        if n > prog.isrs[lvl].arrivals_per_period:
            raise ValueError(
                f"承诺的 {lvl} 每周期到达数 {n} 超过分析上界 "
                f"{prog.isrs[lvl].arrivals_per_period}，不能写入交付配置"
            )

    return {
        "schema": "fwstack/config/v1",
        "program": {
            "name": prog.name,
            "path": _relpath(prog.source_path),
            "fingerprint": fingerprint(prog.source_text),
        },
        "board": {
            "name": board.name,
            "path": _relpath(board.source_path),
            "fingerprint": fingerprint(board.source_text),
        },        "stack_model": stack_model,
        "guarantees": {
            "foreground_peak_bytes": max(m.fg_peak for m in result.mode_reports.values()),
            "shared_peak_bytes": result.plans[SHARED].peak,
            "interrupt_peak_bytes": max(
                lr.nested_peak for lr in result.level_reports.values()
            ),
            "worst_latency_us": {
                lvl: lr.worst_latency for lvl, lr in result.level_reports.items()
            },
            "worst_response_us": {
                lvl: lr.worst_response for lvl, lr in result.level_reports.items()
            },
            "deadline_us": dict(board.deadlines),
            "period_load_us": result.period_load,
            "sample_period_us": board.sample_period,
            "arrivals_per_period": dict(arrivals),
        },
        "memory_layout": [asdict(r) for r in plan.regions],
        "open_points": [asdict(op) for op in result.open_points],
        "modes": {m.name: m.entry for m in prog.modes.values()},
    }


def save_config(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if cfg.get("schema") != "fwstack/config/v1":
        raise ValueError(f"{path}: 不支持的配置格式 {cfg.get('schema')!r}")
    return cfg


def verify_fingerprints(cfg: dict, base_dir: str = ".") -> list[str]:
    """对照当前源文件校验指纹，返回失效原因列表（空列表=配置仍然有效）。"""
    problems: list[str] = []
    for kind in ("program", "board"):
        rel = cfg[kind]["path"]
        src = rel if os.path.isabs(rel) else os.path.join(base_dir, rel)
        if not os.path.exists(src):
            problems.append(f"{kind} 源文件 {rel} 已不存在，无法回放")
            continue
        with open(src, "r", encoding="utf-8") as fh:
            text = fh.read()
        actual = fingerprint(text)
        if actual != cfg[kind]["fingerprint"]:
            problems.append(
                f"{kind} 指纹失配：配置基于 {cfg[kind]['fingerprint']}，"
                f"当前源文件为 {actual}（开放中断点或保留区已变动），"
                "原峰值/响应保证作废，需重新分析并交付"
            )
    return problems


def _relpath(path: str | None) -> str:
    if path is None:
        return ""
    return os.path.relpath(path, os.getcwd()) if os.path.isabs(path) else path
