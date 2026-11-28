#!/usr/bin/env bash
# 完全离线的共享光纤教具：一键复跑（仅依赖 Python3 标准库）。
set -euo pipefail
cd "$(dirname "$0")"

echo "== 0/4 自测数值核心（分数单纯形 + 多面体） =="
python3 tests/test_lp_core.py
python3 tests/test_framing.py

echo
echo "== 1/4 由虚构真值生成区间观测（仅生成器与最终核验可读真值） =="
python3 tools/make_observations.py

echo
echo "== 2/4 维护分析：区间观测 -> 损耗可行多面体 -> 签发维护依据 =="
python3 src/patrol_analysis.py

echo
echo "== 3/4 接入台：门禁 + 双方案调度 + 双驱动 + 重组核验 + 两笔反事实 =="
python3 src/verify.py

echo
echo "== 3b/4 接入台用移交半平面独立重建复核 =="
python3 src/access_desk_check.py

echo
echo "== 4/4 端到端回归断言 =="
python3 tests/test_end_to_end.py

echo
echo "全部完成。产物见 outputs/ ："
ls -1 outputs/
