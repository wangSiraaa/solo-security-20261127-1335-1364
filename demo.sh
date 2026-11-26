#!/usr/bin/env bash
# 实际演练：录入、交班、迟到签字、口述人取消、档案查询、刊次冻结与勘误。
set -u

DESK=/workspace/desk
BASE=/workspace/runs
DATE=2026-11-26
rm -rf "$BASE"
mkdir -p "$BASE"

banner() {
  printf '\n\n==================== %s ====================\n' "$1"
}

run() {
  local root=$1; shift
  printf '\n$ desk --root %q' "$root"
  printf ' %q' "$@"
  printf '\n'
  "$DESK" --root "$root" "$@"
}

must_fail() {
  local root=$1; shift
  printf '\n$ desk --root %q' "$root"
  printf ' %q' "$@"
  printf '   # 预期被拒绝\n'
  set +e
  "$DESK" --root "$root" "$@"
  local rc=$?
  set -u
  if [ "$rc" -eq 0 ]; then
    echo "测试失败：上面的命令本应被拒绝"
    exit 1
  fi
  echo "[符合预期：拒绝码=$rc]"
}

make_baseline() {
  local r=$1
  "$DESK" --root "$r" init --force >/dev/null
  "$DESK" --root "$r" presence confirm >/dev/null
  "$DESK" --root "$r" badge tap --signer a >/dev/null
  "$DESK" --root "$r" segment add --by a \
    --text '民国三十七年冬天，我在码头仓库里抄写进出口货单。江雾天天漫到门槛，油灯总被水汽打暗。' >/dev/null
  "$DESK" --root "$r" sign --segment seg-0001 --signer a \
    --out "$r/a-seg1.json" >/dev/null
  "$DESK" --root "$r" receive "$r/a-seg1.json" >/dev/null
  "$DESK" --root "$r" buffer set --by a \
    --text '那天江雾很重，我刚写下“桐油二百' >/dev/null
  "$DESK" --root "$r" buffer seal --as a >/dev/null
  # 甲的签认器生成了应答，但短暂掉线；文件留在 outbox，交班时程序尚不知道它有效。
  "$DESK" --root "$r" sign --segment seg-0002 --signer a \
    --out "$r/a-seg2-late.json" >/dev/null
  "$DESK" --root "$r" badge leave --signer a >/dev/null
  "$DESK" --root "$r" presence confirm >/dev/null
  "$DESK" --root "$r" badge tap --signer b >/dev/null
  "$DESK" --root "$r" handover create --from a --to b --presence latest >/dev/null
}

banner '1. 主流程：甲独自录段并正常入档'
M=$BASE/01-main
run "$M" init --force
run "$M" presence confirm
run "$M" badge tap --signer a
run "$M" segment add --by a --text '民国三十七年冬天，我在码头仓库里抄写进出口货单。江雾天天漫到门槛，油灯总被水汽打暗。'
run "$M" sign --segment seg-0001 --signer a --out "$M/outbox/a-seg1.json"
run "$M" receive "$M/outbox/a-seg1.json"

banner '2. 半句进入缓冲并封成甲的待签私稿；签字应答掉线'
run "$M" buffer set --by a --text '那天江雾很重，我刚写下“桐油二百'
run "$M" buffer seal --as a
run "$M" sign --segment seg-0002 --signer a --out "$M/outbox/a-seg2-late.json"
run "$M" badge leave --signer a

banner '3. 乙到场：工位只证明有人，乙的个人签认器才证明是乙'
run "$M" presence confirm
run "$M" badge tap --signer b
run "$M" handover create --from a --to b --presence latest
must_fail "$M" segment show seg-0002 --as b
run "$M" segment show seg-0001 --as b
must_fail "$M" sign --segment seg-0002 --signer b --out "$M/outbox/try-b-as-a.json"

banner '4. 乙趁口述人在场，以自己身份新录、签字并请口述人复核'
run "$M" review schedule --narrator n --handover HO-0001
run "$M" segment add --by b --kind relief_dictation --review-id RV-0001 \
  --text '后来三条驳船趁雾靠岸。脚夫扛着木箱上下跳板，一直忙到天快亮。'
run "$M" sign --segment seg-0003 --signer b --out "$M/outbox/b-seg3.json"
run "$M" handover deliver HO-0001 "$M/outbox/b-seg3.json"
run "$M" review respond RV-0001 --signer n --decision confirmed \
  --reason '乙读回全文，口述人点头确认' --out "$M/outbox/n-rv1-ok.json"
run "$M" handover deliver HO-0001 "$M/outbox/n-rv1-ok.json"

banner '5. 同一个交班请求 HO-0001 收到甲的迟到签字'
run "$M" handover deliver HO-0001 "$M/outbox/a-seg2-late.json"
run "$M" --json handover show HO-0001

banner '6. 档案查询端调取可署名成稿，然后签发当日唯一刊次'
run "$M" archive query --date "$DATE" --format markdown
run "$M" publish --date "$DATE" --work oral-001 --clerk b
must_fail "$M" publish --date "$DATE" --work oral-001 --clerk b
run "$M" erratum add --edition ED-0001 --signer a --target-segment seg-0001 \
  --correction '第一段中的“进出口货单”应勘为“进口货单”；甲签字时漏看了“出口”二字的误记。' \
  --reason '刊次发出后甲重新核对原始纸片'
run "$M" archive query --date "$DATE" --format markdown > "$M/archive-query-after-erratum.txt"
echo "完整查询结果已保存：$M/archive-query-after-erratum.txt"
sed -n '1,45p' "$M/archive-query-after-erratum.txt"

banner '7. 追溯：甲的合格签字给了哪段、当时谁能看见'
run "$M" trace seg-0001
run "$M" trace seg-0002
"$DESK" --root "$M" --json trace seg-0001 > "$M/trace-seg0001.json"
"$DESK" --root "$M" --json trace seg-0002 > "$M/trace-seg0002.json"
echo "结构化追溯已保存：$M/trace-seg0001.json, $M/trace-seg0002.json"

banner '8. 安全反例：集体在场不能顶替甲；改一个字后原签字失效'
N=$BASE/02-negative
run "$N" init --force
run "$N" presence confirm
run "$N" badge tap --signer a
run "$N" segment add --by a --text '甲独自核对的原段：渡轮照例鸣笛三声。'
run "$N" sign --segment seg-0001 --signer a --out "$N/a-seg1.json"
run "$N" receive "$N/a-seg1.json"
run "$N" segment add --by a --text '这一段只补送集体在场凭证，不能变成甲的签字。'
printf '\n$ desk --root %q --json presence confirm > presence.json\n' "$N"
"$DESK" --root "$N" --json presence confirm > "$N/presence.json"
must_fail "$N" receive "$N/presence.json"
run "$N" sign --segment seg-0002 --signer a --out "$N/a-seg2-personal.json"
python3 - "$N" <<'PY'
import json, sys
root = sys.argv[1]
p = f"{root}/a-seg2-personal.json"
d = json.load(open(p, encoding="utf-8"))
presence = json.load(open(f"{root}/presence.json", encoding="utf-8"))
d["envelope"] = presence
out = f"{root}/presence-instead-of-personal.json"
json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(out)
PY
must_fail "$N" receive "$N/presence-instead-of-personal.json"

run "$N" segment add --by a --text '签字队列原文：渡轮鸣笛三声，钟声也敲了三下。'
run "$N" sign --segment seg-0003 --signer a --out "$N/a-seg3-valid.json"
python3 - "$N" <<'PY'
import json, sys
root = sys.argv[1]
p = f"{root}/a-seg3-valid.json"
d = json.load(open(p, encoding="utf-8"))
assert "三" in d["text"]
d["text"] = d["text"].replace("三", "五", 1)  # 只改队列待发正文中的一个字
out = f"{root}/a-seg3-tampered-one-char.json"
json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(out)
PY
must_fail "$N" receive "$N/a-seg3-tampered-one-char.json"
run "$N" receive "$N/a-seg3-valid.json"
run "$N" archive query --date "$DATE" --format markdown

banner '9. 同一交班请求的分支比较：迟到甲签 vs 口述人取消'
L=$BASE/03-cmp-late
C=$BASE/04-cmp-cancel
B=$BASE/05-cmp-both
make_baseline "$L"
make_baseline "$C"
make_baseline "$B"

echo
echo '--- 分支 L：只补送甲的迟到签字 ---'
"$DESK" --root "$L" handover deliver HO-0001 "$L/a-seg2-late.json"
"$DESK" --root "$L" --json archive query --date "$DATE" > "$L/archive.json"

echo
echo '--- 分支 C：口述人临时取消复核，不产生乙的新复核段 ---'
"$DESK" --root "$C" review schedule --narrator n --handover HO-0001
"$DESK" --root "$C" review respond RV-0001 --signer n --decision cancelled \
  --scope review_appointment --reason '口述人身体不适，今天不能来台复核' \
  --out "$C/n-cancel.json"
"$DESK" --root "$C" handover deliver HO-0001 "$C/n-cancel.json"
"$DESK" --root "$C" --json archive query --date "$DATE" > "$C/archive.json"

echo
echo '--- 分支 BOTH：同一个 HO-0001 分别收到甲迟到签字和口述人取消 ---'
"$DESK" --root "$B" review schedule --narrator n --handover HO-0001
"$DESK" --root "$B" review respond RV-0001 --signer n --decision cancelled \
  --scope review_appointment --reason '口述人临时取消' \
  --out "$B/n-cancel.json"
"$DESK" --root "$B" handover deliver HO-0001 "$B/n-cancel.json"
"$DESK" --root "$B" handover deliver HO-0001 "$B/a-seg2-late.json"
"$DESK" --root "$B" --json archive query --date "$DATE" > "$B/archive.json"

for pair in "L:$L" "CANCEL:$C" "BOTH:$B"; do
  name=${pair%%:*}; r=${pair#*:}
  echo
  echo "[$name] $r"
  jq -r '.segments[] | [.segment_id, (.attributed_label // "不署名"), (.signature_valid|tostring), (.include_in_named_draft|tostring), (.chars|tostring), .text] | @tsv' "$r/archive.json"
  echo -n '可进具名成稿字符数（含标点）: '
  jq '([.segments[] | select(.include_in_named_draft) | .chars] | add) // 0' "$r/archive.json"
  echo -n '严格复核确证层字符数（含标点）: '
  jq '([.segments[] | select(.include_in_verified_edition) | .chars] | add) // 0' "$r/archive.json"
done

banner '10. 若“取消”是撤回刊布同意而非仅取消预约：乙的新录段也会被挡住'
G=$BASE/06-consent-withdrawal
make_baseline "$G"
"$DESK" --root "$G" review schedule --narrator n --handover HO-0001 >/dev/null
"$DESK" --root "$G" segment add --by b --kind relief_dictation --review-id RV-0001 \
  --text '乙刚录下、尚待口述人同意刊布的一句。' >/dev/null
"$DESK" --root "$G" sign --segment seg-0003 --signer b --out "$G/b3.json" >/dev/null
"$DESK" --root "$G" handover deliver HO-0001 "$G/b3.json" >/dev/null
"$DESK" --root "$G" review respond RV-0001 --signer n --decision cancelled \
  --scope publication_consent --reason '口述人要求暂不公开这一段' \
  --out "$G/n-consent-withdraw.json" >/dev/null
"$DESK" --root "$G" handover deliver HO-0001 "$G/n-consent-withdraw.json"
"$DESK" --root "$G" handover deliver HO-0001 "$G/a-seg2-late.json"
"$DESK" --root "$G" --json archive query --date "$DATE" > "$G/archive.json"
jq -r '.segments[] | [.segment_id, (.attributed_label // "不署名"), (.include_in_named_draft|tostring), (.publication_consent_blocked|tostring), .text] | @tsv' "$G/archive.json"

banner '11. 若甲的签字在刊次发出后才到：签字可存档，但不能改写当日刊次，只能走勘误'
P=$BASE/07-late-after-publication
make_baseline "$P"
run "$P" publish --date "$DATE" --work oral-001 --clerk b
run "$P" handover deliver HO-0001 "$P/a-seg2-late.json"
must_fail "$P" publish --date "$DATE" --work oral-001 --clerk b
run "$P" erratum add --edition ED-0001 --signer a --target-segment seg-0002 \
  --correction '甲在刊后才收到的签字确认残句：“那天江雾很重，我刚写下‘桐油二百”。该句作为勘误/补遗附于 ED-0001，不插入已冻结正文，也不产生第二个当日正式刊次。' \
  --reason '甲的个人签认应答在刊次发出后才送达'
"$DESK" --root "$P" --json archive query --date "$DATE" > "$P/archive.json"
jq -r '.official_edition.segment_manifest[].segment_id' "$P/archive.json" | sed 's/^/冻结刊次仍只含: /'

echo
echo "全部演练完成。数据与档案在 $BASE；主档案：$M/archive-query-after-erratum.txt"
python3 /workspace/check_demo.py "$BASE"
