#!/usr/bin/env bash
# 端到端演示：第八代完整卷、第九代片段补卷、跨代矛盾失效、收窄范围和重启取回。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$ROOT/seal-cabinet"
WORK="${1:-$(mktemp -d /tmp/sealcab-demo-XXXXXX)}"
mkdir -p "$WORK"

green() { printf '\n\033[1;32m[PASS]\033[0m %s\n' "$*"; }
blue()  { printf '\n\033[1;36m[STEP]\033[0m %s\n' "$*"; }
fail()  { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

jk() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

assert_py() {
  python3 - "$1" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    r = json.load(f)
if not r.get("ok", False) and r.get("status") not in ("complete", "partial"):
    raise SystemExit(f"verification not OK: {r.get('status')} {r.get('conflicts')}")
expected = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else None
if expected is not None:
    got = r.get("verified_blocks", [])
    if got != expected:
        raise SystemExit(f"verified_blocks mismatch: got {got}, expected {expected}")
status = sys.argv[3] if len(sys.argv) > 3 else None
if status and r.get("status") != status:
    raise SystemExit(f"status mismatch: {r.get('status')} != {status}; conflicts={r.get('conflicts')}")
PY
}

expect_rc() {
  local want="$1"; shift
  set +e
  "$@" >"$WORK/expect-${want}.out" 2>"$WORK/expect-${want}.err"
  local rc=$?
  set -e
  if [[ "$rc" != "$want" ]]; then
    cat "$WORK/expect-${want}.out" || true
    cat "$WORK/expect-${want}.err" >&2 || true
    fail "期望退出码 $want，实际 $rc：$*"
  fi
}

echo "封存柜演示工作目录：$WORK"

# ---------------------------------------------------------------------------
blue "分支一：先占住 A8/B8 两个新位置，封第八代完整时点副本"
export SEALCAB_HOME="$WORK/case8"
"$CLI" init --json > "$WORK/01-init8.json"
S7="$(jk old_seal_id < "$WORK/01-init8.json")"
"$CLI" scenario gen8 >/dev/null

O8="$("$CLI" order submit --generation 8 --all --code DA-G8-COMPLETE --json | jk order_id)"
"$CLI" copy receive "$O8" A --from-source >/dev/null
"$CLI" copy receive "$O8" B --from-source >/dev/null
S8="$("$CLI" seal "$O8" --json | jk seal_id)"
echo "第七代旧卷：$S7"
echo "第八代封条：$S8"

"$CLI" deliver "$S8" --to "独立受托人-甲" \
  --scope-note "完整时点副本：A8/B8 新块，C7/D7 连续接回" \
  --bundle-out "$WORK/case8-delivery/bundle" --json > "$WORK/02-deliver8.json"
"$CLI" verify --bundle "$WORK/case8-delivery/bundle" "$S8" \
  --receipt "$WORK/case8-delivery/delivery-receipt.json" \
  --retrieve-to "$WORK/reviewer8" --json > "$WORK/03-verify8.json"
assert_py "$WORK/03-verify8.json" "A,B,C,D" "complete"
grep -q "A8" "$WORK/reviewer8/A-catalog.txt"
grep -q "B8" "$WORK/reviewer8/B-signoff.txt"
green "独立复核人凭封条、副本和见证取回 A8/B8/C7/D7，结论为完整时点副本"

blue "重启后按原封条号取回原副本"
"$CLI" restart --json > "$WORK/04-restart8.json"
"$CLI" fetch "$S8" --out "$WORK/fetch8" --json > "$WORK/05-fetch8.json"
[[ -f "$WORK/fetch8/A-catalog.txt" && -f "$WORK/fetch8/B-signoff.txt" ]]
green "重启后原封条 $S8 仍可取回原副本"

blue "第九代到来：原 G8 封条不得改写，两个 WORM 位置不得用 B9/C9 覆盖"
"$CLI" scenario gen9 >/dev/null
O9A="$("$CLI" order submit --generation 9 --blocks B,C --code DA-G9-LATER --json | jk order_id)"
expect_rc 4 "$CLI" copy receive "$O9A" B --from-source
expect_rc 4 "$CLI" copy receive "$O9A" C --from-source
S8_AGAIN="$("$CLI" seal "$O8" --json | jk seal_id)"
[[ "$S8_AGAIN" == "$S8" ]]
"$CLI" verify "$S8" --json > "$WORK/06-verify8-after-gen9.json"
assert_py "$WORK/06-verify8-after-gen9.json" "A,B,C,D" "complete"
green "B8 未被 B9 覆盖；重复封原单只返回原封条，第八代一直有效"

blue "第九代成对证据另出补件：派生独立补卷介质并锚定原 G8 封条"
H8="$SEALCAB_HOME"
SUP="$WORK/case8-supplement"
"$CLI" supplement-open --home "$SUP" --from-home "$H8" --predecessor "$S8" --json > "$WORK/06a-supplement-open.json"
export SEALCAB_HOME="$SUP"
O9S="$("$CLI" order submit --generation 9 --blocks B,C --code DA-G9-SUPPLEMENT --predecessor "$S8" --json | jk order_id)"
"$CLI" copy receive "$O9S" B --from-source >/dev/null
"$CLI" copy receive "$O9S" C --from-source >/dev/null
S9S="$("$CLI" seal "$O9S" --json | jk seal_id)"
"$CLI" export-bundle "$S9S" --out "$WORK/supplement9-bundle" >/dev/null
"$CLI" verify --bundle "$WORK/supplement9-bundle" "$S9S" \
  --retrieve-to "$WORK/reviewer-supplement9" --json > "$WORK/06b-verify-supplement9.json"
assert_py "$WORK/06b-verify-supplement9.json" "B,C" "partial"
python3 - "$WORK/supplement9-bundle/seal.json" "$S8" <<'PY'
import json, sys
w = json.load(open(sys.argv[1], encoding="utf-8"))
assert w["body"]["predecessor_seal_id"] == sys.argv[2]
PY
grep -q "B9" "$WORK/reviewer-supplement9/B-signoff.txt"
grep -q "C9" "$WORK/reviewer-supplement9/C-handover.txt"
[[ ! -e "$WORK/reviewer-supplement9/A-catalog.txt" ]]
export SEALCAB_HOME="$H8"
green "复核员从 G9 补件实际取回 B9/C9；补件锚定 $S8，原凭据未被改写"

blue "清机后源端旧字节不可补读，但已封副本仍可复核/取回"
"$CLI" source wipe-blobs --yes >/dev/null
expect_rc 1 "$CLI" source block A
"$CLI" verify "$S8" --retrieve-to "$WORK/reviewer8-after-wipe" --json > "$WORK/07-verify8-wiped.json"
assert_py "$WORK/07-verify8-wiped.json" "A,B,C,D" "complete"
green "源端清机后不能补读旧代；柜内封条、见证快照和 WORM 块仍可取回"

blue "开场问题：镜像拷到一半换卷，先收 A8 后才收到 B9，不能封成 G8"
export SEALCAB_HOME="$WORK/half-switch"
"$CLI" init >/dev/null
"$CLI" scenario gen8 >/dev/null
OH="$("$CLI" order submit --generation 8 --all --code DA-HALF-SWITCH --json | jk order_id)"
"$CLI" copy receive "$OH" A --from-source >/dev/null
"$CLI" scenario gen9 >/dev/null
"$CLI" copy receive "$OH" B --from-source >/dev/null
expect_rc 3 "$CLI" seal "$OH" --json
python3 - "$WORK/expect-3.out" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["error"] == "CROSS_GENERATION_PROOF_FAILED"
assert any(c["code"] == "cross_generation_conflict" for c in r["details"]["conflicts"])
PY
green "A8 与迟到的 B9 虽各自正确，也不能证明同一个第八代时点；该单不予封条"

# ---------------------------------------------------------------------------
blue "分支二：先交已封旧卷，两个空位留给 B9/C9，出具第九代限定片段补卷"
export SEALCAB_HOME="$WORK/case9"
"$CLI" init --json > "$WORK/10-init9.json"
S7B="$(jk old_seal_id < "$WORK/10-init9.json")"

# 工位没有在第八代换卷瞬间抓到 A8/B8；源端操作继续走到第九代。
"$CLI" scenario gen9 >/dev/null
O8FALSE="$("$CLI" order submit --generation 8 --all --code DA-G8-MISSED --json | jk order_id)"
expect_rc 3 "$CLI" seal "$O8FALSE" --json
green "未实际取得 A8/B8 时，不能用 A7/B7 旧块冒充第八代完整卷"

O9="$("$CLI" order submit --generation 9 --blocks B,C --code DA-G9-PAIR --json | jk order_id)"
"$CLI" copy receive "$O9" B --from-source >/dev/null
"$CLI" copy receive "$O9" C --from-source >/dev/null
S9="$("$CLI" seal "$O9" --json | jk seal_id)"
echo "第九代补卷封条：$S9"

"$CLI" deliver "$S9" --to "独立受托人-乙" \
  --scope-note "限定范围片段：仅证明 B9/C9 成对共存" \
  --bundle-out "$WORK/case9-delivery/bundle" --json > "$WORK/11-deliver9.json"
"$CLI" verify --bundle "$WORK/case9-delivery/bundle" "$S9" \
  --receipt "$WORK/case9-delivery/delivery-receipt.json" \
  --retrieve-to "$WORK/reviewer9" --json > "$WORK/12-verify9.json"
assert_py "$WORK/12-verify9.json" "B,C" "partial"
grep -q "B9" "$WORK/reviewer9/B-signoff.txt"
grep -q "C9" "$WORK/reviewer9/C-handover.txt"
[[ ! -e "$WORK/reviewer9/A-catalog.txt" ]]
[[ ! -e "$WORK/case9-delivery/bundle/replica/A.bin" ]]
green "复核员取回成对 B9/C9；这是限定范围片段，看不到 A 块内容"

O10="$("$CLI" order submit --generation 10 --blocks B,C --code DA-G10-WAIT --json | jk order_id)"
expect_rc 2 "$CLI" seal "$O10" --json
green "没有第十代见证和实收件时，回执为缺证待补，而不是完整/片段结论"

# ---------------------------------------------------------------------------
blue "矛盾演示：A/B 本均来自第七代；只替换一次跨代见证，整卷证明失效"
export SEALCAB_HOME="$WORK/fault"
"$CLI" init --json > "$WORK/20-init-fault.json"
S7F="$(jk old_seal_id < "$WORK/20-init-fault.json")"
"$CLI" export-bundle "$S7F" --out "$WORK/good7" >/dev/null
"$CLI" source snapshot --out "$WORK/src7" >/dev/null
"$CLI" scenario gen8 >/dev/null

"$CLI" demo-witness-swap --bundle "$WORK/good7" --out "$WORK/bad-witness" --json > "$WORK/21-swap.json"
expect_rc 3 "$CLI" verify --bundle "$WORK/bad-witness" "$S7F" --json
green "只替换一条跨代见证记录后，原封条号复核判定整卷失效"

MIX_JSON="$("$CLI" demo-mix --bundle "$WORK/good7" --out "$WORK/bad-mix" --json)"
FORGED="$(printf '%s' "$MIX_JSON" | jk forged_seal_number)"
expect_rc 3 "$CLI" verify --bundle "$WORK/bad-mix" "$FORGED" --json
python3 - "$WORK/expect-3.out" <<'PY'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["status"] == "invalid"
assert r["block_self_hashes_valid"] is True
assert any(c.get("code") == "cross_generation_conflict" for c in r["conflicts"])
PY
green "A7 与 B8 各自哈希正确，但源见证证明它们从未在同一代状态共存"

blue "还原来源，提交只含签收页 B 的新单"
"$CLI" source restore --input "$WORK/src7" --yes >/dev/null
OB="$("$CLI" order submit --generation 7 --blocks B --code DA-B-ONLY-001 --json | jk order_id)"
"$CLI" copy receive "$OB" B --from-source >/dev/null
SB="$("$CLI" seal "$OB" --json | jk seal_id)"
echo "签收页窄范围封条：$SB"

"$CLI" export-bundle "$SB" --out "$WORK/b-only-bundle" >/dev/null
"$CLI" verify --bundle "$WORK/b-only-bundle" "$SB" \
  --retrieve-to "$WORK/reviewer-b-only" --json > "$WORK/22-verify-b-only.json"
assert_py "$WORK/22-verify-b-only.json" "B" "partial"
[[ -f "$WORK/reviewer-b-only/B-signoff.txt" ]]
[[ ! -e "$WORK/reviewer-b-only/A-catalog.txt" ]]
[[ ! -e "$WORK/b-only-bundle/replica/A.bin" ]]
green "收窄委托后，复核员仍能收到 B7 签收页，但包内没有目录 A"

blue "再次重启，并按新封条号取回原副本"
"$CLI" restart --json > "$WORK/23-restart-fault.json"
"$CLI" fetch "$SB" --out "$WORK/fetch-b-only" --json > "$WORK/24-fetch-b.json"
[[ -f "$WORK/fetch-b-only/B-signoff.txt" ]]
[[ ! -f "$WORK/fetch-b-only/A-catalog.txt" ]]
green "重启后窄范围原副本仍可按封条号取回"

echo
echo "全部演示断言通过。"
echo "检查目录：$WORK"
echo "可独立复核示例："
echo "  $CLI verify --bundle '$WORK/case9-delivery/bundle' '$S9' --receipt '$WORK/case9-delivery/delivery-receipt.json' --retrieve-to /tmp/retrieve9"
