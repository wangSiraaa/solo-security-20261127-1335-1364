#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "== 运行单元/安全属性测试 =="
python3 -m unittest discover -s tests -v

echo
echo "== 运行完整办证、示证、开席、中断、交席演示（holder + registry 两种模式） =="
python3 -m labbench demo --mode both --dir runtime/demo
