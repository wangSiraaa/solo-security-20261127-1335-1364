#!/usr/bin/env bash
# End-to-end local demonstration. No network or real audio/video is used.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="$HERE/bin/simulcast-access"
NV="$HERE/bin/new-viewer"
OV="$HERE/bin/old-viewer"
TT="$HERE/bin/relay-turntable"
DIRECT_ROOT="$HERE/runtime/direct"
RELAY_ROOT="$HERE/runtime/relay"
REVISION="$HERE/director_inputs/revision-SUB-002-v2.txt"

section() {
  printf '\n\n================ %s ================\n' "$1"
}

must_fail() {
  set +e
  "$@" >/tmp/subtitle-sim-expected-failure.out 2>&1
  local code=$?
  set -e
  if [[ $code -eq 0 ]]; then
    echo "错误：本应被策略拒绝，却成功执行: $*" >&2
    cat /tmp/subtitle-sim-expected-failure.out >&2
    exit 1
  fi
  echo "[预期拒绝，exit=$code] $(tail -n 1 /tmp/subtitle-sim-expected-failure.out)"
}

rm -rf "$DIRECT_ROOT" "$RELAY_ROOT"

section "1. 初始化：能力声明、场记同意、钥匙与受托读音表"
"$A" init --root "$DIRECT_ROOT" --force
"$A" login --root "$DIRECT_ROOT" --seat new-1
"$A" login --root "$DIRECT_ROOT" --seat old-1

section "2. 登录成功不等于已核对本期会话约定"
must_fail "$A" send --root "$DIRECT_ROOT" --sequence BEFORE-NEGOTIATION --text "登录后也不能直接发"

section "3. 场记选择直接共同文字约定 direct-common-text/v1"
"$A" negotiate --root "$DIRECT_ROOT" \
  --consent "$DIRECT_ROOT/trusted/consents/CONS-A-17-DIRECT-OLD1.consent.json"

section "4. 一条普通字幕：两席真显示，再办同文确认"
"$A" send --root "$DIRECT_ROOT" --sequence SUB-001 --version 1 \
  --text "今晚联播准时开始。" \
  --note "场记备注：散场后核对明日唱词；此句不影响同文正文。"
"$NV" view MSG-SUB-001-v1 --root "$DIRECT_ROOT"
"$OV" view MSG-SUB-001-v1 --root "$DIRECT_ROOT"
"$A" confirm-same-text --root "$DIRECT_ROOT" --sequence SUB-001 --version 1
"$A" verify-proof "$DIRECT_ROOT/access/proofs/MSG-SUB-001-v1.same-text.proof.json" \
  --root "$DIRECT_ROOT"

section "5. 导播送入亲自写的改词；两端分别取显示稿和签名回单"
"$A" send --root "$DIRECT_ROOT" --sequence SUB-002 --version 2 \
  --body-file "$REVISION" --kind director-revision \
  --note "场记备注：这是导播自写改词；新席可见，旧席只收共同正文。"
"$NV" view MSG-SUB-002-v2 --root "$DIRECT_ROOT"
"$OV" view MSG-SUB-002-v2 --root "$DIRECT_ROOT"
echo "新席回单: $DIRECT_ROOT/seats/new-1/receipts/MSG-SUB-002-v2.receipt.json"
echo "旧席回单: $DIRECT_ROOT/seats/old-1/receipts/MSG-SUB-002-v2.receipt.json"
echo "新席显示稿: $DIRECT_ROOT/seats/new-1/screens/MSG-SUB-002-v2.screen.txt"
echo "旧席显示稿: $DIRECT_ROOT/seats/old-1/screens/MSG-SUB-002-v2.screen.txt"
"$A" confirm-same-text --root "$DIRECT_ROOT" --sequence SUB-002 --version 2

section "6. 约定仍有效，但旧席停留在改词前版本：不能确认见过新词"
"$A" send --root "$DIRECT_ROOT" --sequence SUB-003 --version 1 --text "改词前的普通句子。"
"$NV" view MSG-SUB-003-v1 --root "$DIRECT_ROOT" >/dev/null
"$OV" view MSG-SUB-003-v1 --root "$DIRECT_ROOT" >/dev/null
"$A" send --root "$DIRECT_ROOT" --sequence SUB-003 --version 2 --text "改词后的新词。"
"$NV" view MSG-SUB-003-v2 --root "$DIRECT_ROOT" >/dev/null
must_fail "$A" confirm-same-text --root "$DIRECT_ROOT" --sequence SUB-003 --version 2

section "7. 只把新席能力声明改成另一场次：旧会话立即失效，须重建"
"$A" set-scene --root "$DIRECT_ROOT" --seat new-1 --scene B-209
must_fail "$A" send --root "$DIRECT_ROOT" --sequence SUB-004 --text "场次不一致，不应发送"
must_fail "$A" negotiate --root "$DIRECT_ROOT" \
  --consent "$DIRECT_ROOT/trusted/consents/CONS-A-17-DIRECT-OLD1.consent.json"
"$A" set-scene --root "$DIRECT_ROOT" --seat new-1 --scene A-17
"$A" issue-consent --root "$DIRECT_ROOT" \
  --profile direct-common-text/v1 --seats new-1,old-1 \
  --consent-id CONS-REBUILT-A17
"$A" negotiate --root "$DIRECT_ROOT" \
  --consent "$DIRECT_ROOT/trusted/consents/CONS-REBUILT-A17.consent.json"

section "8. 联播中途更换旧席 old-2：原约定不无声沿用，须新同意/新会话"
"$A" suspend-session --root "$DIRECT_ROOT" \
  --reason "联播中途旧席 old-1 故障，换上另一台旧席 old-2" \
  --replacement-seat old-2
"$A" issue-consent --root "$DIRECT_ROOT" \
  --profile direct-common-text/v1 --seats new-1,old-2 \
  --consent-id CONS-A17-DIRECT-OLD2
"$A" negotiate --root "$DIRECT_ROOT" \
  --consent "$DIRECT_ROOT/trusted/consents/CONS-A17-DIRECT-OLD2.consent.json"
"$A" send --root "$DIRECT_ROOT" --sequence SUB-005 --version 1 \
  --text "old-2 已重新核对约定，可以同文交接。"
"$NV" view MSG-SUB-005-v1 --root "$DIRECT_ROOT" >/dev/null
"$OV" --seat old-2 view MSG-SUB-005-v1 --root "$DIRECT_ROOT"
"$A" confirm-same-text --root "$DIRECT_ROOT" --sequence SUB-005 --version 1
"$A" verify-proof "$DIRECT_ROOT/access/proofs/MSG-SUB-005-v1.same-text.proof.json" \
  --root "$DIRECT_ROOT" | tail -n 3
echo "换机后，旧 SES-A-17-001 时期的 SUB-001 历史证明仍可独立验证，但不能用于新消息："
"$A" verify-proof "$DIRECT_ROOT/access/proofs/MSG-SUB-001-v1.same-text.proof.json" \
  --root "$DIRECT_ROOT" | tail -n 2

section "9. 另一套安排：受托转台分别终结两段通道并逐段转换"
"$A" init --root "$RELAY_ROOT" --force >/dev/null
"$A" negotiate --root "$RELAY_ROOT" \
  --consent "$RELAY_ROOT/trusted/consents/CONS-A-17-RELAY-OLD1.consent.json"
RELAY_NOTE=$'@relay 改词中的方音：{DY-01}；{DY-02} 注意拖腔。\n@duty 此段仅供当班，不交给转台、旧席或观众稿。'
"$A" send --root "$RELAY_ROOT" --sequence SUB-002 --version 2 \
  --body-file "$REVISION" --kind director-revision --note "$RELAY_NOTE"
"$TT" process-job JOB-MSG-SUB-002-v2 --root "$RELAY_ROOT"
"$TT" show-cert MSG-SUB-002-v2 --root "$RELAY_ROOT"
"$NV" view MSG-SUB-002-v2 --root "$RELAY_ROOT"
"$OV" view MSG-SUB-002-v2 --root "$RELAY_ROOT"
"$A" confirm-relay --root "$RELAY_ROOT" --sequence SUB-002 --version 2
"$A" verify-proof "$RELAY_ROOT/access/proofs/MSG-SUB-002-v2.relay-handover.proof.json" \
  --root "$RELAY_ROOT" | tail -n 4

section "演示完成"
cat <<EOF
可检查：
  直接约定运行根: $DIRECT_ROOT
  转台约定运行根: $RELAY_ROOT
  观众可见稿目录: <root>/audience/
  值班备注目录  : <root>/duty/
  两端显示稿    : <root>/seats/<seat>/screens/
  两端回单      : <root>/seats/<seat>/receipts/
  联播证明      : <root>/access/proofs/
  转台逐段证明  : <relay-root>/access/conversion-certs/
  转台审计      : <relay-root>/audit/audit.jsonl

场记可把 access/proofs 中的证明和 trusted/keyring.json 公钥副本交给导播、
联播审计或其他核对者；verify-proof 会验证两端回单、会话约定、同意记录、
能力声明摘要以及（转台模式下）只能由转台签名的转换证明。
EOF
