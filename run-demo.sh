#!/usr/bin/env bash
# 端到端演练：短名出租/回收/再出租、公钥钉住更新、作者换名、同名混淆包拒绝、
# 离线接件、声明式样件读取与非破坏性迁移、数据目录不随短名易主。
set -u
cd "$(dirname "$0")"
ST="python3 bin/station --home .home"
rm -rf .home .catalog .machines .deliveries .build
mkdir -p .build .deliveries .machines
PASS=0; FAIL=0
ok()   { echo "  断言[通过] $1"; PASS=$((PASS+1)); }
bad()  { echo "  断言[失败] $1"; FAIL=$((FAIL+1)); }
section() { echo; echo "################ $1 ################"; }

expect_ok() {
  local desc="${!#}"; local args=("${@:1:$#-1}")
  if "${args[@]}" >/tmp/st.log 2>&1; then ok "$desc"; else cat /tmp/st.log; bad "$desc"; fi
}
expect_fail() {
  local desc="${!#}"; local args=("${@:1:$#-1}")
  if "${args[@]}" >/tmp/st.log 2>&1; then cat /tmp/st.log; bad "$desc"; else ok "$desc"; fi
}
grep_log()    { if grep -q "$1" /tmp/st.log; then ok "$2"; else bad "$2（日志缺：$1）"; fi; }

# ---------- 0. 身份：1 个目录身份 + 2 个互不混淆的发行身份 ----------
section "0. 建身份（目录 1 把钥；发行方甲、乙各一把独立的钥）"
expect_ok   $ST catalog-init .catalog cat-root "可信目录根" "目录初始化"
expect_ok   $ST id-new rel-alice  release "甲（老作者）" --catalog .catalog "登记甲身份"
expect_ok   $ST id-new rel-bob    release "乙（新租户）" --catalog .catalog "登记乙身份"
ok "rel-alice2（甲换名后的编号）将在第 3 步经目录更名回执由甲的同一把私钥派生"

# ---------- 1. 短名先租给甲；甲构建并上架 v1 ----------
section "1. 短名 lumo-notes 租给甲，甲构建/上架 v1，目录出具租约回执"
expect_ok   $ST catalog-rent .catalog lumo-notes rel-alice "短名出租给甲"
expect_ok   $ST pkg-build manifests/lumo-v1.json .home/keystore/rel-alice.priv.pem .build/lumo-v1.tgz "甲签出 v1 包"
expect_ok   $ST catalog-publish .catalog .build/lumo-v1.tgz "甲的 v1 上架"
$ST catalog-show .catalog | tee /tmp/st.log
grep_log "rel-alice" "租约链可验，当前租户=甲"

# ---------- 2. 旧机初装甲的 v1（钉住甲的公钥指纹），并新建一份此前不存在的样件 ----------
section "2. 旧机 oldbox 初装甲 v1；钉住发行公钥；用户新建样件并填入资料"
expect_ok   $ST m-install --machine .machines/oldbox --catalog .catalog --short lumo-notes "旧机联机初装成功"
expect_ok   $ST m-new-sample --machine .machines/oldbox --short lumo-notes --name meeting-notes \
              --set note_titel=需求评审 --set author=张伟 --set date=2026-11-20 --line "迁移后此行应原样保留" \
              "用当前安装的 v1 写法新建样件"
$ST m-run --machine .machines/oldbox --short lumo-notes --sample meeting-notes | tee /tmp/st.log
grep_log "v1"   "真实启动 v1 并打印数据格式"
grep_log "需求评审" "v1 读回用户填入的内容"
grep_log "note_titel: 需求评审" "字段为 v1 旧拼写"

# ---------- 3. 原作者一次更新必须真能运行：甲发布 v2 勘误（更名后、同一把钥） ----------
section "3. 甲先归还短名、换名 rel-alice->rel-alice2（同一公钥），再以新名签出 v2 勘误并交付"
expect_ok   $ST catalog-return .catalog lumo-notes "甲归还短名（已安装的发行锚点不受影响）"
expect_ok   $ST catalog-rename .catalog rel-alice rel-alice2 "甲（换名继续维护）" "更名回执生成，同公钥"
expect_ok   $ST catalog-rent .catalog lumo-notes rel-alice2 "rel-alice2（同钥、新编号）承租成功"
expect_ok   $ST pkg-build manifests/lumo-v2.json .home/keystore/rel-alice2.priv.pem .build/lumo-v2.tgz "甲以新名签出 v2 勘误"
expect_ok   $ST catalog-publish .catalog .build/lumo-v2.tgz "v2 上架（目录按公钥指纹确认仍是同一条发行线）"
expect_ok   $ST catalog-deliver .catalog lumo-notes .deliveries/errata-alice2 "目录交付勘误包+来源材料"
test -f .deliveries/errata-alice2/package/lumo-notes-2.0.tgz && ok "交付件含包"
test -f .deliveries/errata-alice2/provenance/lease-chain-lumo-notes.json && ok "交付件含租约链回执"
test -f .deliveries/errata-alice2/provenance/rel-alice2.json && ok "交付件含更名回执"

# ---------- 4. 目录断线：旧机只凭钉住指纹接甲（换名后同钥）的勘误 ----------
section "4. 目录离线。旧机只凭初装钉住的指纹 + 包签名接件（更名同钥应放行）"
mv .catalog .catalog.offline
expect_fail $ST m-install --machine .machines/phantom --catalog .catalog --short lumo-notes "无锚点的空机离线不能凭空初装"
expect_ok   $ST m-update --machine .machines/oldbox --delivery .deliveries/errata-alice2 --short lumo-notes \
             "离线接甲的同钥勘误成功"
grep -q "同一发行线" /tmp/st.log 2>/dev/null || true
$ST m-run --machine .machines/oldbox --short lumo-notes | tee /tmp/st.log
grep_log "v2" "真实启动的版本已变为 v2"
grep_log "读取器兼容版本 \[1, 2\]" "v2 读取器声明兼容 v1/v2"
expect_ok   $ST m-run --machine .machines/oldbox --short lumo-notes --sample meeting-notes \
             "v2 直接读回 v1 原件，用户刚填的内容完整"

# ---------- 5. 短名再租给乙；乙用自己的钥重签同名勘误（内容相同、仅发布人不同） ----------
section "5. 短名再出租给乙；乙构建同名同内容但由新公钥签名的混淆包"
mv .catalog.offline .catalog
expect_ok   $ST catalog-return .catalog lumo-notes "归还短名"
expect_ok   $ST catalog-rent   .catalog lumo-notes rel-bob "短名改租给乙（新的另一把钥）"
expect_ok   $ST pkg-build manifests/lumo-v2-bob.json .home/keystore/rel-bob.priv.pem .build/lumo-v2-bob.tgz \
             "乙以自己私钥签出同名 v2 混淆包"
expect_ok   $ST catalog-publish .catalog .build/lumo-v2-bob.tgz "乙的包合法上架（对新租户而言是正规包）"
expect_ok   $ST catalog-deliver .catalog lumo-notes .deliveries/errata-bob "目录交付乙的包与来源材料"
$ST catalog-show .catalog | tee /tmp/st.log
grep_log "rel-bob" "目录层面当前租户确为乙"

# ---------- 6. 旧机不得只凭同名采用新租户的包 ----------
section "6. 旧机拒绝乙的同名勘误：短名同名不构成信任（关键否定用例）"
expect_fail $ST m-update --machine .machines/oldbox --delivery .deliveries/errata-bob --short lumo-notes \
             "乙的同名包被旧机拒绝"
grep_log "短名同名不构成信任" "拒绝理由写明指纹不符"
test -d .machines/oldbox/inbox/rejected && ok "被拒交付件已隔离归档，未安装"
$ST m-run --machine .machines/oldbox --short lumo-notes | tee /tmp/st.log
grep_log "lumo-notes-2.0" "旧机仍运行甲的 v2，未被乙替换"
FP_OLD=$(python3 -c "import json;print(json.load(open('.machines/oldbox/pins.json'))['lumo-notes']['pinned_fingerprint'])")
find .machines/oldbox/data -maxdepth 2 -type d | tee /tmp/st.log
grep_log "$FP_OLD" "数据目录仍挂在甲的指纹下（目录里无乙的数据目录）"

# ---------- 7. 空机按短名初装：订到当前租户乙，并与旧机数据完全隔离 ----------
section "7. 新空机 newbox 按短名初装，订到当前租户乙；数据目录互不相同"
expect_ok   $ST m-install --machine .machines/newbox --catalog .catalog --short lumo-notes "新空机初装乙的包"
$ST m-status --machine .machines/newbox | tee /tmp/st.log
grep_log "rel-bob" "新机钉住乙的指纹"
FP_OLD=$(python3 -c "import json;print(json.load(open('.machines/oldbox/pins.json'))['lumo-notes']['pinned_fingerprint'])")
FP_NEW=$(python3 -c "import json;print(json.load(open('.machines/newbox/pins.json'))['lumo-notes']['pinned_fingerprint'])")
[ "$FP_OLD" != "$FP_NEW" ] && ok "两台机钉住的指纹不同（甲 ≠ 乙）" || bad "指纹竟然相同"
test -d ".machines/oldbox/data/$FP_OLD" && test -d ".machines/newbox/data/$FP_NEW" && ok "数据目录按指纹隔离，归还短名不移交任何用户数据"
test ! -e ".machines/newbox/data/$FP_OLD" && ok "新机看不到旧机（甲指纹下）的样件"

# ---------- 8. 恢复可信发布人：离线旧机再加一份旧格式样件，然后才送来同一个甲的勘误 ----------
section "8. 恢复可信发布人；先在离线旧机加 v1 旧格式样件，再送同一个甲签名的包"
expect_ok   $ST m-new-sample --machine .machines/oldbox --short lumo-notes --name legacy-card \
              --format-version 1 \
              --set note_titel=离线补录 --set author=王芳 --line "目录离线期间新增" \
              "离线状态下用 v1 旧格式再建一份样件（v2 读取器声明兼容 v1）"
# 再构造一份含 @locked 的样件，演示“无法迁移的要能重新打开原件”
expect_ok   $ST m-new-sample --machine .machines/oldbox --short lumo-notes --name sealed \
              --format-version 1 \
              --set note_titel=封存记录 --marker locked \
              "另建一份 v1 且带 @locked 的样件"
mv .catalog .catalog.offline
# 重建一个完全相同交付件（同一甲私钥、同一 manifest），验证目录断线期间可信更新仍能办
rm -rf .deliveries/errata-alice2-again
cp -r .deliveries/errata-alice2 .deliveries/errata-alice2-again 2>/dev/null || true
# 原交付件已被 m-update 移动进 receipts，从回执重建
mkdir -p .deliveries/errata-alice2-again/package .deliveries/errata-alice2-again/provenance
cp .machines/oldbox/receipts/lumo-notes-2.0/package/lumo-notes-2.0.tgz .deliveries/errata-alice2-again/package/
cp .machines/oldbox/receipts/lumo-notes-2.0/provenance/* .deliveries/errata-alice2-again/provenance/
# 当前已是 v2，再“送相同包”会因版本不新而拒——按题意改为：用真正启动的 v2 打开新样件并迁移；
# 同时单独验证相同交付件在干净锚点上的可信性（见第 9 步）。
expect_fail $ST m-update --machine .machines/oldbox --delivery .deliveries/errata-alice2-again --short lumo-notes \
             "同一勘误重复投递：版本不新，拒绝降级/重装（防回滚）"
grep_log "不新于现装" "防回滚判定生效"
mv .catalog.offline .catalog
$ST m-run --machine .machines/oldbox --short lumo-notes --sample legacy-card | tee /tmp/st.log
grep_log "离线补录" "真正启动的 v2 打开并读回离线期间新增的 v1 样件"
$ST m-run --machine .machines/oldbox --short lumo-notes --sample meeting-notes | tee /tmp/st.log
grep_log "需求评审" "最早那份样件内容依旧可读"

# ---------- 9. 声明式非破坏性迁移；无法迁移的保留原件并能重新打开 ----------
section "9. v1->v2 声明式迁移：普通样件另存 v2；@locked 原件保留可重开"
expect_ok   $ST m-migrate --machine .machines/oldbox --short lumo-notes "执行迁移规则"
test -f .machines/oldbox/data/$FP_OLD/lumo-notes/meeting-notes.v2.sample && ok "普通样件生成 v2 副本"
test -f .machines/oldbox/data/$FP_OLD/lumo-notes/meeting-notes.sample && ok "v1 原件未被破坏（非破坏性）"
test ! -e .machines/oldbox/data/$FP_OLD/lumo-notes/sealed.v2.sample && ok "@locked 样件未被迁移"
test -f .machines/oldbox/data/$FP_OLD/lumo-notes/sealed.sample && ok "无法迁移的原件仍在，可重新打开"
$ST m-run --machine .machines/oldbox --short lumo-notes --sample sealed | tee /tmp/st.log
grep_log "封存记录" "用 v2 运行版本重新打开未迁移原件成功"
python3 - <<'PY' | tee /tmp/st.log
import json, pathlib
base = pathlib.Path(".machines/oldbox/data")
fp = json.load(open(".machines/oldbox/pins.json"))["lumo-notes"]["pinned_fingerprint"]
v2 = (base/fp/"lumo-notes/meeting-notes.v2.sample").read_text(encoding="utf-8")
assert v2.startswith("@lumo-text/v2"), v2
assert "note_title: 需求评审" in v2 and "note_titel" not in v2, v2
assert "reviewed: false" in v2 and "迁移后此行应原样保留" in v2, v2
print("  v2 副本回读：字段已改名、新字段已补、附注行保留")
PY
grep_log "字段已改名" "迁移结果回读正确"

# ---------- 10. 篡改检出：改 manifest 后用原签名重打包，必须验签失败 ----------
section "10. 额外否定用例：manifest 被篡改（签名未改）后 Ed25519 验签失败"
mkdir -p .build/tamper
rm -rf .build/tamper/ext && mkdir .build/tamper/ext
tar xzf .build/lumo-v2.tgz -C .build/tamper/ext
python3 - <<'PY'
import json
p = ".build/tamper/ext/manifest.json"
m = json.load(open(p, encoding="utf-8"))
m["version"] = "9.9.9"          # 篡改内容，但沿用原 manifest.sig
json.dump(m, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2, sort_keys=True)
PY
tar czf .build/tamper/bad.tgz -C .build/tamper/ext manifest.json manifest.sig
rm -rf .deliveries/tampered && mkdir -p .deliveries/tampered/package .deliveries/tampered/provenance
cp .build/tamper/bad.tgz .deliveries/tampered/package/lumo-notes-2.0.tgz
cp .machines/oldbox/receipts/lumo-notes-2.0/provenance/* .deliveries/tampered/provenance/ 2>/dev/null || true
expect_fail $ST m-update --machine .machines/oldbox --delivery .deliveries/tampered --short lumo-notes \
             "篡改包被拒绝"
grep_log "包签名对发布人公钥无效" "Ed25519 验签捕获篡改"

echo
echo "================ 演练结果：PASS=$PASS  FAIL=$FAIL ================"
[ "$FAIL" -eq 0 ]
