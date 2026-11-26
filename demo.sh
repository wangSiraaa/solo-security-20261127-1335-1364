#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
SCEN_ROOT="${WATCHDECK_DEMO_ROOT:-/tmp/watchdesk-demo}"
rm -rf "$SCEN_ROOT"
mkdir -p "$SCEN_ROOT"

make_base() {
  local d="$1"
  rm -rf "$d"
  mkdir -p "$d"
  local W=(python3 -m watchdesk --state "$d")

  "${W[@]}" init --start "2026-11-26T12:00:00+08:00" --beat-seconds 60 >/dev/null
  "${W[@]}" tick --beats 2 >/dev/null
  "${W[@]}" register examples/rules-v1.json >/dev/null
  "${W[@]}" enable seal3 --version 2026.11.26-v1 --by a-heng \
    --receipt EN-SEAL-V1 --from-beat 1 --desk old-seat --shift noon >/dev/null
  "${W[@]}" enable pair2 --version 2026.11.26-v1 --by xiao-song \
    --receipt EN-PAIR-V1 --from-beat 1 --desk old-seat --shift noon >/dev/null

  # 心跳只是存活证据；这里故意保留，后续查询会显示它不能替代处理回执。
  "${W[@]}" heartbeat --beat 1 --by a-heng --desk old-seat --shift noon --receipt HB-SEAL-1 >/dev/null
  "${W[@]}" heartbeat --beat 2 --by xiao-song --desk old-seat --shift noon --receipt HB-PAIR-2 >/dev/null

  "${W[@]}" load examples/events-prefix.jsonl >/dev/null
  "${W[@]}" ack --seq 1 --rule seal3 --by a-heng --shift noon --desk old-seat --receipt ACK-SEAL-1 >/dev/null
  "${W[@]}" ack --seq 1 --rule pair2 --by xiao-song --shift noon --desk old-seat --receipt ACK-PAIR-1 >/dev/null
  "${W[@]}" ack --seq 2 --rule seal3 --by a-heng --shift noon --desk old-seat --receipt ACK-SEAL-2 >/dev/null
  "${W[@]}" ack --seq 2 --rule pair2 --by xiao-song --shift noon --desk old-seat --receipt ACK-PAIR-2 >/dev/null
}

run_matrix_case() {
  local pos="$1" carry="$2"
  local base="$SCEN_ROOT/base"
  local d="$SCEN_ROOT/emergency-${pos}-carry-${carry}"
  cp -a "$base" "$d"
  local W=(python3 -m watchdesk --state "$d")

  echo
  echo "================ 紧急离岗：复位在临界拍${pos} / 携带${carry} ================"
  "${W[@]}" tick --beats 1 >/dev/null

  local select_args=()
  if [[ "$carry" == "seal" ]]; then
    select_args=(--select ctx-seal3-1)
  fi
  "${W[@]}" plan --at-beat 2 --slots 1 --emergency --by duty-lead \
    --from-shift noon --from-desk old-seat \
    --to-shift afternoon --to-desk new-seat --to-owner ji-cheng \
    "${select_args[@]}"
  "${W[@]}" execute --plan HO-001 --by duty-lead --receipt "CARRY-${pos}-${carry}"

  if [[ "$pos" == "inside" ]]; then
    "${W[@]}" load examples/events-inside-tail.jsonl >/dev/null
    "${W[@]}" ack --seq 3 --beat 3 --rule seal3 --rule pair2 \
      --by ji-cheng --shift afternoon --desk new-seat --receipt "ACK-3-${carry}"

    if [[ "$carry" == "pair" ]]; then
      "${W[@]}" dispatch ALR-pair2-3 --by ji-cheng --message-id "MSG-${pos}-${carry}" >/dev/null
      "${W[@]}" deliver ALR-pair2-3 --group seal-duty-group \
        --received-by oncall-lead --receipt "DLV-${pos}-${carry}"
      "${W[@]}" signout seal3 --from-beat 3 --to-beat 3 --shift afternoon \
        --by ji-cheng --desk new-seat --receipt CERT-SEAL-NEW-PREFIX
    else
      "${W[@]}" signout seal3 --from-beat 1 --to-beat 3 \
        --shift noon-to-afternoon --by duty-lead --desk joint-desk \
        --receipt CERT-SEAL-CHAIN
    fi
  else
    # 第3拍没有新设备事件，但值班台仍逐拍轮询，处理高水位停在 seq2。
    "${W[@]}" ack --beat 3 --through-seq 2 --rule seal3 --rule pair2 \
      --by ji-cheng --shift afternoon --desk new-seat --receipt ACK-POLL-BEAT3
    if [[ "$carry" == "pair" ]]; then
      "${W[@]}" signout seal3 --from-beat 3 --to-beat 3 --shift afternoon \
        --by ji-cheng --desk new-seat --receipt CERT-SEAL-REBUILD-BEAT3
    else
      "${W[@]}" signout seal3 --from-beat 1 --to-beat 3 \
        --shift noon-to-afternoon --by duty-lead --desk joint-desk \
        --receipt CERT-SEAL-CHAIN
    fi

    "${W[@]}" tick --beats 1 >/dev/null
    "${W[@]}" load examples/events-outside-tail.jsonl >/dev/null
    "${W[@]}" ack --seq 3 --beat 4 --rule pair2 \
      --by ji-cheng --shift afternoon --desk new-seat --receipt "ACK-RESET-4-${carry}" || true
  fi

  "${W[@]}" audit --rule pair2 || true
  echo "---------------- 覆盖查询 ----------------"
  "${W[@]}" coverage seal3 --from-beat 1 --to-beat 3
  echo "---------------- 交班报告 ----------------"
  "${W[@]}" report | tee "$d/report.txt" >/dev/null
  echo "完整报告: $d/report.txt"
}

echo "构造紧急离岗共同前提：三拍封存已走过前两拍，短规则刚收到松动。"
make_base "$SCEN_ROOT/base"

run_matrix_case inside pair
run_matrix_case inside seal
run_matrix_case outside pair
run_matrix_case outside seal

echo
echo "================ 成功交班：启用 + 逐拍处理 + 提醒送达 ================"
SUCCESS="$SCEN_ROOT/success"
cp -a "$SCEN_ROOT/base" "$SUCCESS"
W=(python3 -m watchdesk --state "$SUCCESS")
"${W[@]}" tick --beats 1 >/dev/null
"${W[@]}" plan --at-beat 2 --slots 2 --by duty-lead \
  --from-shift noon --from-desk old-seat \
  --to-shift afternoon --to-desk new-seat --to-owner ji-cheng
"${W[@]}" execute --plan HO-001 --by duty-lead --receipt CARRY-BOTH
"${W[@]}" load examples/events-inside-tail.jsonl >/dev/null
"${W[@]}" ack --seq 3 --beat 3 --rule seal3 --rule pair2 \
  --by ji-cheng --shift afternoon --desk new-seat --receipt ACK-SUCCESS-3
"${W[@]}" dispatch ALR-pair2-3 --by ji-cheng --message-id MSG-SUCCESS >/dev/null
"${W[@]}" deliver ALR-pair2-3 --group seal-duty-group \
  --received-by group-oncall --receipt DLV-SUCCESS
"${W[@]}" stop seal3 --through-beat 3 --by duty-lead --receipt STOP-SEAL >/dev/null
"${W[@]}" stop pair2 --through-beat 3 --by duty-lead --receipt STOP-PAIR >/dev/null
"${W[@]}" signout seal3 --from-beat 1 --to-beat 3 \
  --shift noon-to-afternoon --by duty-lead --receipt CERT-SUCCESS-SEAL
"${W[@]}" signout pair2 --from-beat 1 --to-beat 3 \
  --shift noon-to-afternoon --by duty-lead --receipt CERT-SUCCESS-PAIR
"${W[@]}" report | tee "$SUCCESS/report.txt"

echo
echo "================ 漏掉中间拍处理回执 ================"
MISS="$SCEN_ROOT/missing-middle"
rm -rf "$MISS" && mkdir -p "$MISS"
W=(python3 -m watchdesk --state "$MISS")
"${W[@]}" init >/dev/null
"${W[@]}" register examples/rules-v1.json >/dev/null
"${W[@]}" enable seal3 --version 2026.11.26-v1 --by a-heng --receipt EN-M --from-beat 1 >/dev/null
"${W[@]}" heartbeat --beat 2 --by a-heng --receipt HB-MISSING-MIDDLE >/dev/null
"${W[@]}" load examples/events-inside.jsonl >/dev/null
"${W[@]}" ack --seq 1 --rule seal3 --by a-heng --receipt ACK-M1
# 故意没有第2拍 processing.ack；心跳 HB-MISSING-MIDDLE 不能补位。
"${W[@]}" ack --seq 3 --rule seal3 --by a-heng --receipt ACK-M3
"${W[@]}" coverage seal3 --from-beat 1 --to-beat 3 || true
"${W[@]}" signout seal3 --from-beat 1 --to-beat 3 --shift noon \
  --by duty-lead --receipt CERT-MISSING
"${W[@]}" rebuild

echo
echo "================ 还原进度后只撤销消息组正式签收 ================"
REVOKE="$SCEN_ROOT/revoke-delivery"
cp -a "$SUCCESS" "$REVOKE"
W=(python3 -m watchdesk --state "$REVOKE")
"${W[@]}" rebuild
"${W[@]}" revoke-delivery DLV-SUCCESS --by compliance \
  --reason "该组正式收讫章系误盖；未取得接收组签收" --receipt REVOKE-DLV-1
"${W[@]}" audit --rule pair2
"${W[@]}" report | tee "$REVOKE/report.txt"

echo
echo "================ 规则换版证据 ================"
VER="$SCEN_ROOT/version-switch"
rm -rf "$VER" && mkdir -p "$VER"
W=(python3 -m watchdesk --state "$VER")
"${W[@]}" init >/dev/null
"${W[@]}" register examples/rules-v1.json >/dev/null
"${W[@]}" register examples/rules-v2.json >/dev/null
"${W[@]}" enable pair2 --version 2026.11.26-v1 --by xiao-song --receipt EN-P-V1 --from-beat 1 >/dev/null
"${W[@]}" stop pair2 --through-beat 2 --by duty-lead --receipt STOP-P-V1 >/dev/null
"${W[@]}" enable pair2 --version 2026.11.26-v2 --by ji-cheng --receipt EN-P-V2 --from-beat 3 >/dev/null
"${W[@]}" coverage pair2 --from-beat 1 --to-beat 4 || true

echo
echo "全部场景目录: $SCEN_ROOT"
