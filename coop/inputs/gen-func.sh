#!/usr/bin/env bash
# 本地输入生成器：从源码确定地生成两件功能件。
# 两间构建室在干净环境中各自运行本脚本，必须得到逐字节相同的产物。
set -euo pipefail
src="$1"
outdir="$2"
mkdir -p "$outdir"
spec_version=$(sed -n 's/^规格版本: //p' "$src" | tr -d '\r')
modules=$(sed -n 's/^  - //p' "$src")
{
  echo "FUNC1-BEGIN"
  echo "spec=${spec_version}"
  while IFS= read -r m; do echo "module=${m}"; done <<< "$modules"
  echo "FUNC1-END"
} | tr -d '\r' > "$outdir/func1.bin"
# func2 是 func1 的逐行反转摘要，同样确定
{
  echo "FUNC2-BEGIN"
  sed 's/^/line: /' "$outdir/func1.bin"
  sha256sum "$outdir/func1.bin" | cut -d' ' -f1 | sed 's/^/sha256=/'
  echo "FUNC2-END"
} > "$outdir/func2.bin"
