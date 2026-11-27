#!/usr/bin/env bash
# run-demo — 一键复现全部主张与反例。小文本 + 两个本地目录（room1 / room2）即可。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
V=./coop-verify.sh

H()  { printf '\n\033[1;36m━━━━━━━━ %s ━━━━━━━━\033[0m\n' "$*"; }
ok() { printf '\033[32m［预期·成立］\033[0m %s\n' "$*"; }
no() { printf '\033[31m［预期·拒绝］\033[0m %s\n' "$*"; }

# 干净双路径的公共准备：room1 封存旧环境（源建），room2 干净环境（源建）
prep_clean() {
  $V init >/dev/null
  $V register-source >/dev/null
  $V load-env room1 sealed-old >/dev/null
  $V load-env room2 clean >/dev/null
  $V register-dependency room1 typeset-v1 inputs/typeset-v1 "封存旧环境内置排版工具" >/dev/null
  $V register-dependency room2 typeset-v2 inputs/typeset-v2 "干净环境现装排版工具" >/dev/null
  $V set-policy room1 yi >/dev/null
  $V set-policy room2 yi >/dev/null
}

# 同一份迟到独立材料
LATE=/tmp/late-note.txt
cat > "$LATE" <<'EOF'
补充数据表 LATE-2026-11-26（独立材料，临开工送达）
数值: 5150
EOF

# ════════════════════ 第一幕：干净双路径，独立可证 ════════════════════
H "第一幕  两间各自从源码生成：功能件逐字节相同，封面相差一天"
prep_clean
$V build room1
$V build room2
ok "两间 func1/func2 摘要逐字节相同；封面日期 D0=2026-11-25 / D1=2026-11-26"

H "复核员本地发起重建（强制源码路径，不碰预制件）"
$V rebuild-check room1
$V rebuild-check room2

H "先给接受展示差异的乙签发『独立功能复现证明』"
$V issue-cert room2 functional-reproduction
$V verify room2 functional-reproduction

# ════════════════════ 第二幕：只改来源，摘要不动 ════════════════════
H "第二幕  仅把 room2 关键件来源改成另一间复制（摘要与签章都不变）"
$V tamper-origin room2 func1.bin
no "同一摘要、同一签章，来源链一变，原独立性声明立即失效："
$V verify room2 functional-reproduction && echo "不该成功" || true
ok "证明的是：相同摘要 + 两枚签章 ≠ 两条独立生产路径"
$V restore-origin room2 func1.bin >/dev/null
$V verify room2 functional-reproduction >/dev/null && ok "恢复来源登记后查验重新通过"

# ════════════════════ 第三幕：乙改要求封面逐字相同 ════════════════════
H "第三幕  保持独立，仅把乙登记为也要求封面逐字节相同"
$V set-policy room2 yi cover-strict
no "日期差异此时改变乙可领的声明，独立功能复现证明被拒："
$V issue-cert room2 functional-reproduction && echo "不该成功" || true
$V set-policy room2 yi >/dev/null
ok "政策改回『允许展示日期差异』后可重新签发"
$V issue-cert room2 functional-reproduction >/dev/null && ok "重新签发成功"

# ════════════════════ 第四幕：封存旧环境 + 共用预制件（甲/乙分流）════════════════════
H "第四幕  第二间装入封存旧环境：原封面可重放，功能件出自共用预制件"
$V init >/dev/null
$V register-source >/dev/null
$V load-env room1 sealed-old >/dev/null
$V load-env room2 sealed-old >/dev/null
$V register-dependency room1 typeset-v1 inputs/typeset-v1 "封存旧环境内置" >/dev/null
$V register-dependency room2 typeset-v1 inputs/typeset-v1 "封存旧环境内置" >/dev/null
$V build room1 >/dev/null
$V build room2 --use-shared-prebuilt
$V set-policy room1 jia >/dev/null
$V set-policy room2 jia >/dev/null
ok "甲接受如实列明的共享中间件，整包逐字（含同日封面）→ 整包逐字复刻证明："
$V issue-cert room2 verbatim-package
H "同样的包改挂乙订单：独立功能复现证明必须拒签"
$V set-policy room2 yi >/dev/null
no "来源链显示 shared-prebuilt，乙要求分别从源码生成："
$V issue-cert room2 functional-reproduction && echo "不该成功" || true
H "注意：本地强制源码重建仍然逐字节通过——重建比对本身分不出共用预制件"
$V rebuild-check room2 | tail -3
ok "区分两条路径的是来源登记，不是摘要"

# ════════════════════ 第五幕：变化一，临时可取回的排版工具 ════════════════════
H "第五幕  变化一：原排版工具只是临时可取回（未留存副本）"
$V init >/dev/null
$V register-source >/dev/null
$V load-env room1 sealed-old >/dev/null
$V register-dependency room1 typeset-v1 inputs/typeset-v1 "临时可取回的原排版工具" transient >/dev/null
$V set-policy room1 yi >/dev/null
$V build room1 >/dev/null
$V rebuild-check room1 | tail -1
$V retract-tool room1
no "工具收回后：封面无法重建，含封面的可复现承诺不能兑现；功能件复核不受影响："
$V rebuild-check room1 || true
ok "若登记为留存副本（retain），复核员持有副本则承诺仍可兑现——下面演示留存情形"
$V restore-tool room1 >/dev/null
$V init >/dev/null
$V register-source >/dev/null
$V load-env room1 sealed-old >/dev/null
$V register-dependency room1 typeset-v1 inputs/typeset-v1 "临时可取回但已留存副本" transient retain >/dev/null
$V set-policy room1 yi >/dev/null
$V build room1 >/dev/null
$V retract-tool room1
ok "凭登记的留存副本恢复可取回性后，整包重建复核通过："
cp state/retained/typeset-v1 inputs/typeset-v1
$V rebuild-check room1 | tail -3
$V restore-tool room1 >/dev/null

# ════════════════════ 第六幕：变化二，迟到的独立材料 ════════════════════
H "第六幕  变化二：独立材料临开工才送到，必须单独登记"
prep_clean
mkdir -p room1/work/stray && cp "$LATE" room1/work/stray/
no "未登记材料出现在工作区，构建直接被挡："
$V build room1 && echo "不该成功" || true
$V register-late room1 "$LATE" >/dev/null
$V register-late room2 "$LATE" >/dev/null
ok "两间各自单独登记同一份迟到材料后分别构建："
$V build room1 >/dev/null
$V build room2 >/dev/null
$V rebuild-check room1 | tail -1
$V issue-cert room2 functional-reproduction | tail -1
H "反例：只有一间并入迟到材料 → 功能件出现差异，不得归到封面日期里"
prep_clean
$V register-late room2 "$LATE" >/dev/null
$V build room1 >/dev/null
$V build room2 >/dev/null
no "封面差异仍在允许清单内，但功能件摘要不同，独立功能复现证明拒签："
$V issue-cert room2 functional-reproduction && echo "不该成功" || true

H "全部场景结束"
echo "结论：摘要是产物属性，独立性是路径属性；本工具只按来源链回答独立性问题。"
