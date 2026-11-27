#!/usr/bin/env bash
# coop-verify — 离线构建合作社「独立重建」复核本地入口
#
# 登记对象：源码 / 配方 / 输入生成器 / 每次实际构建 / 物件来源
# 核心主张：相同摘要 + 两枚签章 != 两条独立生产路径；独立性必须看来源链。
#
# 子命令一览见文末 usage。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IN="$ROOT/inputs"
STATE="$ROOT/state"
DIST="$ROOT/dist"
D0="2026-11-25"   # 第一间构建室印刷日期
D1="2026-11-26"   # 第二间构建室印刷日期（相差一天）

c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()   { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw()   { printf '\033[33m%s\033[0m\n' "$*"; }
c_blue()  { printf '\033[34m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
die()   { c_red "✗ $*"; exit 1; }

sha() { sha256sum "$1" | cut -d' ' -f1; }
rec_get() { sed -n "s/^$2=//p" "$1" | head -1; }
room_dir() {
  case "$1" in
    room1|room2) echo "$ROOT/$1";;
    *) die "构建室只能是 room1 或 room2（本班只有两间）";;
  esac
}
need_file() { [ -f "$2" ] || die "$1 缺失：$2"; }

# 简单 kv 记录写入
rec_write() {
  local file="$1"; shift
  : > "$file"
  for kv in "$@"; do printf '%s\n' "$kv" >> "$file"; done
}

# ───────────────────────────── init ─────────────────────────────
cmd_init() {
  # 复位演示中被收回的排版工具
  for t in "$IN"/typeset-v*.retracted; do [ -e "$t" ] && mv "$t" "${t%.retracted}"; done
  rm -rf "$STATE" "$DIST" "$ROOT/room1/work" "$ROOT/room2/work" \
         "$ROOT/room1/env.state" "$ROOT/room2/env.state" \
         "$ROOT/room1/policy" "$ROOT/room2/policy" \
         "$ROOT/room1/build.id" "$ROOT/room2/build.id"
  mkdir -p "$STATE/deps" "$STATE/builds" "$STATE/prov" "$STATE/certs" "$DIST"
  mkdir -p "$ROOT/room1/work" "$ROOT/room2/work"
  # 发布人签章密钥对（功能件/证明签章用；两间各自的构建签章在 build 时生成）
  openssl genpkey -algorithm RSA -out "$STATE/publisher.pem" 2>/dev/null
  openssl pkey -in "$STATE/publisher.pem" -pubout -out "$STATE/publisher.pub" 2>/dev/null
  chmod 600 "$STATE/publisher.pem"
  # 预制共享中间件：由生成器从源码生成一次后封存（摘要与干净重建必然相同）
  mkdir -p "$STATE/shared"
  bash "$IN/gen-func.sh" "$IN/source.txt" "$STATE/shared"
  c_grn "✓ 初始化完成：状态库 $STATE/、出厂目录 $DIST/ 已重建"
  info "共享预制件（封存）："
  info "  func1.bin $(sha "$STATE/shared/func1.bin")"
  info "  func2.bin $(sha "$STATE/shared/func2.bin")"
}

# ─────────────────────── register-source ───────────────────────
cmd_register_source() {
  need_file "源码" "$IN/source.txt"; need_file "配方" "$IN/recipe.txt"
  need_file "生成器" "$IN/gen-func.sh"
  rec_write "$STATE/source.reg" \
    "kind=source" "path=$IN/source.txt" "sha256=$(sha "$IN/source.txt")"
  rec_write "$STATE/recipe.reg" \
    "kind=recipe" "path=$IN/recipe.txt" "sha256=$(sha "$IN/recipe.txt")"
  rec_write "$STATE/deps/gen-func.reg" \
    "kind=generator" "name=gen-func.sh" "path=$IN/gen-func.sh" \
    "sha256=$(sha "$IN/gen-func.sh")" "origin=本地生成器" "declared=true"
  c_grn "✓ 已登记源码、配方、输入生成器（含摘要与来源）"
}

# ───────────────────── register-dependency ─────────────────────
# 用法: register-dependency <room> <name> <path> <origin> [transient] [retain]
cmd_register_dependency() {
  local room="$1"; local rd; rd="$(room_dir "$room")"; shift
  local name="$1" path="$2" origin="$3"; shift 3 || true
  local transient=false retain=false
  for f in "$@"; do
    [ "$f" = transient ] && transient=true
    [ "$f" = retain ] && retain=true
  done
  need_file "依赖 $name" "$path"
  local d="$STATE/deps/${room}__${name}.reg"
  rec_write "$d" \
    "kind=dependency" "room=$room" "name=$name" "path=$path" \
    "sha256=$(sha "$path")" "origin=$origin" "declared=true" \
    "transient=$transient" "retain=$retain"
  mkdir -p "$STATE/late"
  c_grn "✓ [$room] 登记依赖 $name（来源：$origin；临时可取回=$transient；留存副本=$retain）"
}

# 登记迟到的独立材料（单独登记，绝不混入既有依赖记录）
cmd_register_late() {
  local room="$1" frag="$2"
  need_file "迟到材料" "$frag"
  local d="$STATE/deps/${room}__late-fragment.reg"
  rec_write "$d" \
    "kind=late-material" "room=$room" "name=latenote" "path=$frag" \
    "sha256=$(sha "$frag")" "origin=临开工送达的独立材料（单独登记）" \
    "declared=true" "arrival=late"
  printf '%s\n' "$frag" > "$STATE/late/${room}.list"
  c_grn "✓ [$room] 迟到独立材料已单独登记：$frag"
}

# ────────────────────────── load-env ──────────────────────────
# 用法: load-env <room> sealed-old|clean
# 装入后本班不能换环境；每间只一个空档。
cmd_load_env() {
  local room="$1" mode="$2"; local rd; rd="$(room_dir "$room")"
  [ -f "$rd/env.state" ] && die "[$room] 环境已装入（$mode 锁定），本班不能更换"
  case "$mode" in
    sealed-old|clean) ;;
    *) die "环境只能是 sealed-old（封存旧环境）或 clean（干净环境）";;
  esac
  local typeset
  if [ "$mode" = sealed-old ]; then typeset="$IN/typeset-v1"; else typeset="$IN/typeset-v2"; fi
  rec_write "$rd/env.state" "room=$room" "env=$mode" "typeset=$typeset" "slot=used"
  c_grn "✓ [$room] 环境已装入并锁定：$mode（排版工具 $typeset）"
}

# ──────────────────────────── build ────────────────────────────
# 用法: build <room> [--use-shared-prebuilt]
cmd_build() {
  local room="$1"; local use_shared=false
  [ "${2:-}" = "--use-shared-prebuilt" ] && use_shared=true
  local rd; rd="$(room_dir "$room")"
  need_file "环境状态（先 load-env）" "$rd/env.state"
  [ -f "$rd/build.id" ] && die "[$room] 本班空档已用于一次构建，不能再建第二次"
  local env typeset; env="$(rec_get "$rd/env.state" env)"; typeset="$(rec_get "$rd/env.state" typeset)"
  local date_str; [ "$room" = room1 ] && date_str="$D0" || date_str="$D1"

  local w="$rd/work/pkg"; rm -rf "$w"; mkdir -p "$w"

  # 构建前清点：工作区里凡未登记的文件一律拦下（未声明材料不得进入功能件）
  local stray="$rd/work/stray"
  if [ -d "$stray" ]; then
    for f in "$stray"/*; do
      [ -e "$f" ] || continue
      local base; base="$(basename "$f")"
      if [ ! -f "$STATE/deps/${room}__late-fragment.reg" ] \
         || [ "$(rec_get "$STATE/deps/${room}__late-fragment.reg" sha256)" != "$(sha "$f")" ]; then
        die "工作区存在未登记材料 $base：先 register-late 单独登记，否则不得用于构建"
      fi
    done
  fi

  # 功能件来源
  local func_origin func_detail
  if [ "$use_shared" = true ]; then
    [ "$env" = sealed-old ] || die "--use-shared-prebuilt 只在封存旧环境中可用"
    cp "$STATE/shared/func1.bin" "$STATE/shared/func2.bin" "$w/"
    rec_write "$STATE/deps/${room}__shared-prebuilt.reg" \
      "kind=dependency" "room=$room" "name=shared-prebuilt" \
      "path=$STATE/shared" "origin=两间共用预制件（共享中间件）" \
      "declared=true" "shared=true"
    func_origin=shared-prebuilt
    func_detail="共用预制件 $STATE/shared（已如实声明）"
  else
    need_file "生成器登记" "$STATE/deps/gen-func.reg"
    bash "$IN/gen-func.sh" "$IN/source.txt" "$w"
    func_origin=source-build
    func_detail="生成器 gen-func.sh 现场从源码生成"
  fi

  # 迟到独立材料：仅当已单独登记才允许并入功能件
  local late_used=none
  if [ -f "$STATE/deps/${room}__late-fragment.reg" ]; then
    local frag; frag="$(rec_get "$STATE/deps/${room}__late-fragment.reg" path)"
    need_file "迟到材料原件" "$frag"
    cp "$frag" "$w/late-fragment.txt"
    printf '\n[迟到材料并入]\n' >> "$w/func1.bin"
    cat "$frag" >> "$w/func1.bin"
    # 重新确定地生成 func2，使其与 func1 保持一致
    ( cd "$w" && {
      echo "FUNC2-BEGIN"; sed 's/^/line: /' func1.bin
      sha256sum func1.bin | cut -d' ' -f1 | sed 's/^/sha256=/'
      echo "FUNC2-END"; } > func2.bin; )
    late_used="$frag"
    c_ylw "  [$room] 已登记的迟到材料并入功能件：$frag"
  fi

  # 共用预制件路径下，旧环境的原排版工具重放原封面（同日同字），整包逐字
  if [ "$use_shared" = true ]; then date_str="$D0"; fi

  # 封面：排版工具必须已登记且实物可取回
  local tname; tname="$(basename "$typeset")"
  local treg="$STATE/deps/${room}__${tname}.reg"
  need_file "排版工具登记（$tname）" "$treg"
  if [ ! -f "$typeset" ]; then
    die "排版工具 $tname 已不可取回（临时依赖未留存），无法重建封面"
  fi
  bash "$typeset" "$date_str" "$w/cover.txt"

  # 打包 + 逐物件摘要 + 构建签章
  local pkg="$rd/work/package.tar"
  tar -cf "$pkg" -C "$w" .
  local sk="$rd/work/buildkey"; local pk="$rd/work/buildkey.pub"
  openssl genpkey -algorithm RSA -out "$sk" 2>/dev/null
  openssl pkey -in "$sk" -pubout -out "$pk" 2>/dev/null
  openssl dgst -sha256 -sign "$sk" -out "$pkg.sig" "$pkg"

  local bid="${room}-$(date +%s)"
  rec_write "$STATE/builds/$bid.rec" \
    "build_id=$bid" "room=$room" "env=$env" "date=$date_str" \
    "package=$pkg" "package_sha256=$(sha "$pkg")" \
    "func1_sha256=$(sha "$w/func1.bin")" "func2_sha256=$(sha "$w/func2.bin")" \
    "cover_sha256=$(sha "$w/cover.txt")" \
    "func_origin=$func_origin" "func_detail=$func_detail" \
    "typeset=$tname" "late_material=$late_used"
  cp "$pkg" "$DIST/${room}.tar"; cp "$pkg.sig" "$DIST/${room}.tar.sig"; cp "$pk" "$DIST/${room}.pub"

  # 逐物件来源登记（复核独立性的关键数据）
  for obj in func1.bin func2.bin; do
    rec_write "$STATE/prov/${room}__${obj}.prov" \
      "room=$room" "object=$obj" "sha256=$(sha "$w/$obj")" \
      "origin=$func_origin" "detail=$func_detail" "declared=true"
  done
  rec_write "$STATE/prov/${room}__cover.txt.prov" \
    "room=$room" "object=cover.txt" "sha256=$(sha "$w/cover.txt")" \
    "origin=typeset" "detail=$tname @ $date_str" "declared=true"
  printf '%s\n' "$bid" > "$rd/build.id"

  c_grn "✓ [$room] 构建完成（$env，功能件来源：$func_origin）"
  info "func1.bin $(sha "$w/func1.bin")"
  info "func2.bin $(sha "$w/func2.bin")"
  info "cover.txt $(sha "$w/cover.txt")  日期=$date_str"
}

# ─────────────────────── rebuild-check ───────────────────────
# 复核员本地发起重建：忽略预制件，强制从源码再生成，逐字节比对。
cmd_rebuild_check() {
  local room="$1"; local rd; rd="$(room_dir "$room")"
  need_file "构建记录" "$rd/build.id"
  local bid; bid="$(cat "$rd/build.id")"
  local b="$STATE/builds/$bid.rec"
  local env; env="$(rec_get "$b" env)"
  local date_str; date_str="$(rec_get "$b" date)"
  local typeset; typeset="$(rec_get "$rd/env.state" typeset)"
  local tmp; tmp="$(mktemp -d)"
  bash "$IN/gen-func.sh" "$IN/source.txt" "$tmp"
  if [ -f "$STATE/deps/${room}__late-fragment.reg" ]; then
    local frag; frag="$(rec_get "$STATE/deps/${room}__late-fragment.reg" path)"
    printf '\n[迟到材料并入]\n' >> "$tmp/func1.bin"; cat "$frag" >> "$tmp/func1.bin"
    ( cd "$tmp" && { echo "FUNC2-BEGIN"; sed 's/^/line: /' func1.bin
      sha256sum func1.bin | cut -d' ' -f1 | sed 's/^/sha256=/'
      echo "FUNC2-END"; } > func2.bin; )
  fi
  local cover_ok=fail cover_note
  if [ -f "$typeset" ]; then
    bash "$typeset" "$date_str" "$tmp/cover.txt"; cover_ok=ok
    cover_note="封面可在本地重新排版并逐字节比对"
  else
    cover_note="排版工具已不可取回，封面无法重建（功能件复核不受影响）"
  fi
  local f1ok=fail f2ok=covok=fail
  local w="$rd/work/pkg"
  cmp -s "$tmp/func1.bin" "$w/func1.bin" && f1ok=ok
  cmp -s "$tmp/func2.bin" "$w/func2.bin" && f2ok=ok
  [ "$cover_ok" = ok ] && cmp -s "$tmp/cover.txt" "$w/cover.txt" && covok=ok
  c_blue "── 本地重建复核 [$room]（强制源码路径，不用预制件）──"
  [ "$f1ok" = ok ] && c_grn "✓ func1.bin 重建逐字节相同" || c_red "✗ func1.bin 重建不一致"
  [ "$f2ok" = ok ] && c_grn "✓ func2.bin 重建逐字节相同" || c_red "✗ func2.bin 重建不一致"
  if [ "$cover_ok" = ok ]; then
    [ "$covok" = ok ] && c_grn "✓ cover.txt 重建逐字节相同" || c_red "✗ cover.txt 重建不一致"
  else
    c_ylw "⚠ $cover_note"
  fi
  rm -rf "$tmp"
  [ "$f1ok" = ok ] && [ "$f2ok" = ok ] || die "功能件重建复核未通过"
  if [ "$cover_ok" != ok ] || [ "$covok" != ok ]; then
    die "封面重建复核未通过（排版工具不可取回或封面不一致）；功能件本身已逐字节复核通过"
  fi
  c_grn "✓ [$room] 整包（功能件+封面）本地重建复核全部通过"
}

# ───────────────────────── set-policy ─────────────────────────
# 用法: set-policy <room> jia|yi [cover-strict]
#   jia = 甲：可逐字复刻整包，接受如实列明的共享中间件
#   yi  = 乙：两间分别从源码生成功能件；默认接受封面日期展示差异
#   cover-strict = 乙追加要求封面也逐字节相同
cmd_set_policy() {
  local room="$1" party="$2"; local strict="${3:-}"
  local rd; rd="$(room_dir "$room")"
  local cover_diff=allow
  [ "$strict" = cover-strict ] && cover_diff=forbid
  case "$party" in jia|yi) ;; *) die "订单方只能是 jia 或 yi";; esac
  rec_write "$rd/policy" "room=$room" "party=$party" "cover_date_diff=$cover_diff"
  c_grn "✓ [$room] 登记订单：$party；封面日期差异=$cover_diff"
}

# ───────────── 来源链/独立性内部判定（不看摘要，看来源） ─────────────
# 输出: echo 结论；非零返回=不独立
evaluate_object() {
  local room="$1" obj="$2" party="$3"
  local p="$STATE/prov/${room}__${obj}.prov"
  need_file "来源记录" "$p"
  local origin; origin="$(rec_get "$p" origin)"
  case "$origin" in
    source-build)
      [ -f "$STATE/deps/gen-func.reg" ] || { echo "来源链断裂：生成器未登记"; return 1; }
      echo "独立（源码现场生成，生成器已登记）"; return 0;;
    shared-prebuilt)
      if [ "$party" = jia ]; then
        echo "非独立但可接受（甲：共享中间件已如实声明）"; return 0
      else
        echo "不独立（乙拒绝共用预制件，要求分别从源码生成）"; return 1
      fi;;
    copied-from-other-room)
      echo "不独立（实际复用另一间成品，原独立性声明失效）"; return 1;;
    *) echo "未知来源 $origin"; return 1;;
  esac
}

# ──────────────────────── issue-cert ─────────────────────────
# 用法: issue-cert <room> functional-reproduction|verbatim-package
cmd_issue_cert() {
  local room="$1" ctype="$2"; local rd; rd="$(room_dir "$room")"
  need_file "订单政策（先 set-policy）" "$rd/policy"
  need_file "构建记录" "$rd/build.id"
  local party; party="$(rec_get "$rd/policy" party)"
  local cover_diff; cover_diff="$(rec_get "$rd/policy" cover_date_diff)"
  local bid; bid="$(cat "$rd/build.id")"
  local b="$STATE/builds/$bid.rec"
  local other; [ "$room" = room1 ] && other=room2 || other=room1
  local ob="$STATE/builds/$(cat "$(room_dir "$other")/build.id").rec"

  c_blue "── 签发审查 [$room] 订单=$party 证明类型=$ctype ──"
  local fail=""
  # 0) 依赖可取回性：临时工具被收回且无留存副本时，含封面的承诺不能签
  local tname0 typeset0
  typeset0="$(rec_get "$rd/env.state" typeset)"; tname0="$(basename "$typeset0")"
  local treg0="$STATE/deps/${room}__${tname0}.reg"
  if [ ! -f "$typeset0" ]; then
    if [ -f "$STATE/retained/$tname0" ]; then
      info "排版工具原件已收回，但留存副本可取回：$STATE/retained/$tname0"
    else
      fail="$fail 排版工具 $tname0 已不可取回且无留存副本，封面可复现性无法支撑；"
    fi
  fi
  # 1) 功能件逐字节相同（摘要相同只说明产物一致，不说明路径独立）
  for obj in func1.bin func2.bin; do
    local h1 h2
    h1="$(rec_get "$STATE/prov/${room}__${obj}.prov" sha256)"
    h2="$(rec_get "$STATE/prov/${other}__${obj}.prov" sha256)"
    if [ "$h1" = "$h2" ]; then info "$obj 两间摘要相同：$h1"; else fail="$fail $obj 功能件摘要不同（存在功能差异）；"; fi
  done
  # 2) 封面差异审查（展示件与功能件分开判断）
  local ch1 ch2; ch1="$(rec_get "$STATE/prov/${room}__cover.txt.prov" sha256)"
  ch2="$(rec_get "$STATE/prov/${other}__cover.txt.prov" sha256)"
  if [ "$ch1" = "$ch2" ]; then
    info "封面逐字节相同"
  else
    if [ "$cover_diff" = allow ]; then
      local n1 n2
      n1="$(sed 's/^\([[:space:]]*印刷日期:\).*/\1 <DATE>/' "$(room_dir "$room")/work/pkg/cover.txt")"
      n2="$(sed 's/^\([[:space:]]*印刷日期:\).*/\1 <DATE>/' "$(room_dir "$other")/work/pkg/cover.txt")"
      if [ "$n1" = "$n2" ]; then
        c_ylw "  封面不同，但归一化日期后逐字相同 → 属允许清单内的展示差异"
      else
        fail="$fail 封面除日期外还有未声明差异；"
      fi
    else
      fail="$fail 封面逐字节相同政策下日期差异不被接受；"
    fi
  fi
  # 3) 来源链独立性（相同摘要本身不证明任何东西）
  for obj in func1.bin func2.bin; do
    local verdict rc
    verdict="$(evaluate_object "$room" "$obj" "$party")" && rc=0 || rc=$?
    info "$obj 来源链：$verdict"
    [ "$rc" -ne 0 ] && fail="$fail $obj：$verdict；"
  done
  # 4) 证明类型与整包逐字要求
  if [ "$ctype" = verbatim-package ]; then
    [ "$ch1" != "$ch2" ] && fail="$fail 整包逐字复刻证明要求封面也相同；"
  fi

  if [ -n "$fail" ]; then
    c_red "✗ 拒绝签发：$fail"
    c_ylw "  发布人可改签的声明：见 issue-cert 的其它证明类型，或调整订单/来源后重试"
    return 1
  fi
  local cert="$STATE/certs/${room}__${ctype}.pem"
  local tmp; tmp="$(mktemp)"
  {
    echo "证书类型=$ctype"
    echo "订单方=$party"
    echo "构建室=$room"
    echo "构建记录=$bid"
    echo "func1_sha256=$(rec_get "$STATE/prov/${room}__func1.bin.prov" sha256)"
    echo "func2_sha256=$(rec_get "$STATE/prov/${room}__func2.bin.prov" sha256)"
    echo "cover_sha256=$ch1"
    echo "封面日期差异政策=$cover_diff"
    echo "func1来源=$(rec_get "$STATE/prov/${room}__func1.bin.prov" origin)"
    echo "func2来源=$(rec_get "$STATE/prov/${room}__func2.bin.prov" origin)"
    echo "结论=功能件两间逐字节相同且来源链符合订单独立性要求"
    echo "签发时间=$(date -u +%FT%TZ)"
  } > "$tmp"
  openssl dgst -sha256 -sign "$STATE/publisher.pem" -out "$cert.sig" "$tmp"
  cp "$tmp" "$cert"; rm -f "$tmp"
  c_grn "✓ 已签发：$cert（发布人签章 $cert.sig）"
}

# ─────────────────────────── verify ───────────────────────────
cmd_verify() {
  local room="$1" ctype="$2"; local rd; rd="$(room_dir "$room")"
  local cert="$STATE/certs/${room}__${ctype}.pem"
  need_file "证书" "$cert"
  c_blue "── 查验出厂包与证书 [$room / $ctype] ──"
  local bad=""
  openssl dgst -sha256 -verify "$STATE/publisher.pub" -signature "$cert.sig" "$cert" >/dev/null \
    && c_grn "✓ 证书发布人签章有效" || bad="$bad 签章无效；"
  # 依赖可取回性复查（临时工具被收回且无副本 → 含封面承诺不再可兑现）
  local vts vtname
  vts="$(rec_get "$rd/env.state" typeset)"; vtname="$(basename "$vts")"
  if [ ! -f "$vts" ] && [ ! -f "$STATE/retained/$vtname" ]; then
    c_red "✗ 排版工具 $vtname 已不可取回且无留存副本，封面可复现性失去支撑"
    bad="$bad 依赖不可取回；"
  fi
  local party; party="$(rec_get "$rd/policy" party)"
  # 重算实物摘要
  local bid; bid="$(cat "$rd/build.id")"; local b="$STATE/builds/$bid.rec"
  local w="$rd/work/pkg"
  for obj in func1.bin func2.bin cover.txt; do
    local claimed actual
    case "$obj" in
      func1.bin) claimed="$(rec_get "$cert" func1_sha256)";;
      func2.bin) claimed="$(rec_get "$cert" func2_sha256)";;
      cover.txt) claimed="$(rec_get "$cert" cover_sha256)";;
    esac
    actual="$(sha "$w/$obj")"
    [ "$claimed" = "$actual" ] && c_grn "✓ $obj 实物摘要与证书一致" || bad="$bad $obj 实物已变更；"
  done
  # 重新评估来源链（防止出证后被改来源）
  for obj in func1.bin func2.bin; do
    local verdict rc
    verdict="$(evaluate_object "$room" "$obj" "$party")" && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then c_grn "✓ 来源链仍成立：$verdict"; else c_red "✗ 来源链失效：$verdict"; bad="$bad $obj 来源链失效；"; fi
  done
  if [ -n "$bad" ]; then c_red "✗ 查验未通过：$bad"; return 1; fi
  c_grn "✓ 查验通过：该证明当前仍可兑现"
}

# ───────────── 演示用变更：改来源 / 改政策 / 撤回临时工具 ─────────────
cmd_tamper_origin() {
  # 把一间关键件来源改成"从另一间复制"
  local room="$1" obj="${2:-func1.bin}"
  local other; [ "$room" = room1 ] && other=room2 || other=room1
  local p="$STATE/prov/${room}__${obj}.prov"
  need_file "来源记录" "$p"
  rec_write "$p" \
    "room=$room" "object=$obj" "sha256=$(rec_get "$p" sha256)" \
    "origin=copied-from-other-room" "detail=从 $other 成品复制（摘要不变）" "declared=true"
  c_ylw "⚠ 已将 [$room] $obj 来源改为 copied-from-other-room（摘要未动，仅来源链变了）"
}
cmd_restore_origin() {
  local room="$1" obj="${2:-func1.bin}"
  local bid; bid="$(cat "$(room_dir "$room")/build.id")"
  local origin; origin="$(rec_get "$STATE/builds/$bid.rec" func_origin)"
  local detail; detail="$(rec_get "$STATE/builds/$bid.rec" func_detail)"
  rec_write "$STATE/prov/${room}__${obj}.prov" \
    "room=$room" "object=$obj" \
    "sha256=$(rec_get "$STATE/prov/${room}__${obj}.prov" sha256)" \
    "origin=$origin" "detail=$detail" "declared=true"
  c_grn "✓ [$room] $obj 来源已恢复为 $origin"
}
cmd_retract_tool() {
  # 模拟临时排版工具被收回：登记了 retain=true 则自动留存可复核副本
  local room="$1"; local rd; rd="$(room_dir "$room")"
  local typeset; typeset="$(rec_get "$rd/env.state" typeset)"
  local tname; tname="$(basename "$typeset")"
  local treg="$STATE/deps/${room}__${tname}.reg"
  local retain=false
  [ -f "$treg" ] && retain="$(rec_get "$treg" retain)"
  if [ "$retain" = true ]; then
    mkdir -p "$STATE/retained"
    cp "$typeset" "$STATE/retained/$tname"
    mv "$typeset" "$typeset.retracted"
    c_ylw "⚠ $tname 已收回；留存副本在 $STATE/retained/$tname（可支撑复核）"
  else
    mv "$typeset" "$typeset.retracted"
    c_ylw "⚠ 排版工具 $tname 已被收回且无留存副本（原件移至 .retracted）"
  fi
}
cmd_restore_tool() {
  local typeset; typeset="$(rec_get "$(room_dir "$1")/env.state" typeset)"
  [ -f "$typeset.retracted" ] && mv "$typeset.retracted" "$typeset"
  c_grn "✓ 排版工具已放回 $typeset"
}

# ───────────────────────── list/inspect ─────────────────────────
cmd_list() {
  c_blue "== 登记总览 =="
  for f in "$STATE"/source.reg "$STATE"/recipe.reg "$STATE"/deps/*.reg; do
    [ -f "$f" ] || continue
    printf '• %s  ' "$(basename "$f")"
    tr '\n' ' ' < "$f"; echo
  done
  echo
  for f in "$STATE"/builds/*.rec; do
    [ -f "$f" ] || continue
    printf '• %s  ' "$(basename "$f")"
    tr '\n' ' ' < "$f"; echo
  done
  echo
  for f in "$STATE"/prov/*.prov; do
    [ -f "$f" ] || continue
    printf '• %s  ' "$(basename "$f")"
    tr '\n' ' ' < "$f"; echo
  done
}

usage() {
  cat <<'EOF'
用法: ./coop-verify.sh <子命令> [参数]
  init                                          初始化状态库、签章密钥与共享预制件
  register-source                               登记源码/配方/输入生成器
  register-dependency <room> <name> <path> <origin> [transient] [retain]
                                                为某间构建室登记依赖及来源
  register-late <room> <fragment>               单独登记临开工送到的独立材料
  load-env <room> sealed-old|clean              装入并锁定环境（本班不可换）
  build <room> [--use-shared-prebuilt]          执行一次实际构建并登记物件来源
  rebuild-check <room>                          复核员本地强制从源码重建比对
  set-policy <room> jia|yi [cover-strict]       登记订单方与封面差异政策
  issue-cert <room> functional-reproduction|verbatim-package
                                                按来源链（非摘要）审查并签发证明
  verify <room> <ctype>                         查验签章、实物摘要与来源链现状
  tamper-origin <room> [func1.bin]              演示：把关键件来源改成另一间复制
  restore-origin <room> [func1.bin]             恢复来源登记
  retract-tool <room> / restore-tool <room>     模拟临时排版工具收回/放回
  list                                          打印全部登记记录
EOF
}

cmd="${1:-}"
[ $# -gt 0 ] && shift || true
case "$cmd" in
  init)                cmd_init "$@";;
  register-source)     cmd_register_source "$@";;
  register-dependency) cmd_register_dependency "$@";;
  register-late)       cmd_register_late "$@";;
  load-env)            cmd_load_env "$@";;
  build)               cmd_build "$@";;
  rebuild-check)       cmd_rebuild_check "$@";;
  set-policy)          cmd_set_policy "$@";;
  issue-cert)          cmd_issue_cert "$@";;
  verify)              cmd_verify "$@";;
  tamper-origin)       cmd_tamper_origin "$@";;
  restore-origin)      cmd_restore_origin "$@";;
  retract-tool)        cmd_retract_tool "$@";;
  restore-tool)        cmd_restore_tool "$@";;
  list)                cmd_list "$@";;
  *) usage; exit 1;;
esac
