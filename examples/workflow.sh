#!/usr/bin/env bash
# 一条可重复运行的值班员工作流：申请 → 批准 → 离站 → 写信 → 两种次序落册。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${1:-$(mktemp -d)/ops}"
SEALCTL=("$ROOT/sealctl" "--state" "$STATE")

"${SEALCTL[@]}" init --force

"${SEALCTL[@]}" apply --trip alpha --seals 1,3,6
"${SEALCTL[@]}" apply --trip beta  --seals 2,4,5
"${SEALCTL[@]}" approve --trip alpha
"${SEALCTL[@]}" approve --trip beta
"${SEALCTL[@]}" depart --all

"${SEALCTL[@]}" write --trip alpha --text "甲-1：西河湾安全。"
"${SEALCTL[@]}" write --trip alpha --seal 3 --text "甲-3：断崖岭可通行。"
"${SEALCTL[@]}" write --trip alpha --seal 6 --text "甲-6：交接完成。"
"${SEALCTL[@]}" write --trip beta  --text "乙-2：水源点已补给。"
"${SEALCTL[@]}" write --trip beta  --seal 4 --text "乙-4：石桥完好。"
"${SEALCTL[@]}" write --trip beta  --seal 5 --text "乙-5：准备折返。"

# 同一批信，分别用两个全新收端状态跑两种抵达次序。
"${SEALCTL[@]}" arrival --order alpha,beta --receiver a-first
"${SEALCTL[@]}" arrival --order beta,alpha --receiver b-first

echo
echo "状态目录：$STATE"
echo "查看乙先到收端：$ROOT/sealctl --state $STATE ledger --receiver b-first"
