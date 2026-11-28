"""命令行入口（演示/手工操作用；自动化验证走 tests/）。

子命令：
  build-bank   渲染工厂母库
  make-license 生成一份外借授权 JSON
  export       按授权导出路线 A / B（或两者）
  workbench    对两根琴条录音做调律判断并合成伴奏
  reader       普通 WAV 读器对比报告
  retune       对 B 路片段做限域移频
"""
from __future__ import annotations

import argparse
import json
import os

from . import bank, exporter, reader, synth, workbench
from .dsp import read_wav, write_wav
from .tuning import Temperament


def _write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="glock")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-bank")
    b.add_argument("root")
    b.add_argument("--variant", default="A")
    b.add_argument("--retained", default="", help="逗号分隔的保留音 MIDI")

    ml = sub.add_parser("make-license")
    ml.add_argument("path")
    ml.add_argument("--notes", required=True,
                    help="midi:层 列表，如 60:0,1,2;62:0,1,2,3")
    ml.add_argument("--tails", default="")
    ml.add_argument("--max-shift", type=float, default=50.0)

    e = sub.add_parser("export")
    e.add_argument("bank_root")
    e.add_argument("license_json")
    e.add_argument("out_dir")
    e.add_argument("--route", choices=["A", "B", "both"], default="both")

    w = sub.add_parser("workbench")
    w.add_argument("wav_a")
    w.add_argument("wav_b")
    w.add_argument("temperament_json")
    w.add_argument("chords_json", help='[{"root":60,"shape":"maj"}, ...]')
    w.add_argument("out_dir")

    r = sub.add_parser("reader")
    r.add_argument("wav_a")
    r.add_argument("wav_b")

    rt = sub.add_parser("retune")
    rt.add_argument("in_wav")
    rt.add_argument("out_wav")
    rt.add_argument("cents", type=float)
    rt.add_argument("--max-shift", type=float, default=50.0)

    args = p.parse_args(argv)

    if args.cmd == "build-bank":
        retained = [int(x) for x in args.retained.split(",") if x]
        bank.build_factory_bank(args.root, variant_tag=args.variant,
                                retained_notes=retained)
        print("factory bank written:", args.root)
        return 0

    if args.cmd == "make-license":
        notes = {}
        for item in args.notes.split(";"):
            if not item:
                continue
            midi, layers = item.split(":")
            notes[int(midi)] = [int(x) for x in layers.split(",")]
        tails = {int(x) for x in args.tails.split(",") if x}
        lic = bank.License(name=os.path.basename(args.path), notes=notes,
                           tails=tails, max_cents_shift=args.max_shift)
        lic.save(args.path)
        print("license written:", args.path)
        return 0

    if args.cmd == "export":
        lic = bank.License.load(args.license_json)
        mat = bank.open_loaned_materials(args.bank_root, lic)
        if args.route == "A":
            exporter.export_route_a(mat, args.out_dir)
        elif args.route == "B":
            exporter.export_route_b(mat, args.out_dir)
        else:
            exporter.export_both(mat, args.out_dir)
        print("export written:", args.out_dir)
        return 0

    if args.cmd == "workbench":
        with open(args.temperament_json, encoding="utf-8") as f:
            temp_spec = json.load(f)
        with open(args.chords_json, encoding="utf-8") as f:
            chord_specs = json.load(f)
        os.makedirs(args.out_dir, exist_ok=True)
        report = workbench.run_workbench(args.wav_a, args.wav_b, temp_spec,
                                         chord_specs)
        _write_json(report, os.path.join(args.out_dir, "report.json"))
        from .analysis import analyze_bar
        from .tuning import load_temperament
        a = analyze_bar(args.wav_a, "bar_A")
        b = analyze_bar(args.wav_b, "bar_B")
        temp = load_temperament(temp_spec)
        for i, spec in enumerate(chord_specs):
            v = workbench.judge_chord(a, b, spec["root"], spec["shape"], temp)
            workbench.render_accompaniment(
                a, b, v, temp,
                os.path.join(args.out_dir, f"accomp_{i}.wav"))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "reader":
        print(json.dumps(reader.reader_report(args.wav_a, args.wav_b),
                         ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "retune":
        sr, data = read_wav(args.in_wav)
        shifted = exporter.retune_segment(data, args.cents, args.max_shift)
        write_wav(args.out_wav, sr, shifted)
        print("retuned:", args.out_wav)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
