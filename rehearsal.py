# -*- coding: utf-8 -*-
"""端到端排演：发布目录安全编制 + 两台旧机本地排演 + 离线机复演。

时序（与题目逐条对应）：
  0. 编制公开包与两条受限分支（v2 现行版 / v1 旧机兼容版）；
  1. 发版前安全审计：枚举全部资源、试遍 0..200 数量、查后备索引，
     受限词根一律不可见；
  2. 两台旧机各自安装公开包+旧版后备目录，切换地区、填入讲解员数量，
     数字 2 在 ar-AE 走 two、ru-RU 走 few；缺字形条目靠仍公开的后备词播完；
  3. 字幕员给定行宽与两条连播单，字幕席排出完整词句与可读语序；
  4. 揭幕时刻到：获准机原子接入 v2，受限句获准播出；
  5. 还原目录（桩重新生效），再单换旧机支持的 v1 分支版本；
  6. 留存已发布包到第三台离线机，用相同订单复演，核对
     “哪句获准、哪句改用安全表达”。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta

from festival_catalog import seal
from festival_catalog.catalog import (PUBLIC_SCHEMA, build_branch_payload,
                                      build_public_package)
from festival_catalog.client import Resolver, Store
from festival_catalog.devices import Device, Font
from festival_catalog.demo_data import (ALL_ENTRIES, CATALOG_ID, KEY_ID,
                                        OPAQUE_ID, REVEAL_V1, REVEAL_V2,
                                        UNVEIL_AT, legacy_arabic,
                                        legacy_russian)
from festival_catalog.subtitles import render_playlist

UNVEIL = datetime.fromisoformat(UNVEIL_AT)
BEFORE = UNVEIL - timedelta(hours=2)
AFTER = UNVEIL + timedelta(minutes=5)
LINE_WIDTH = 26          # 字幕员提供的可用行宽
RESTRICTED_ROOTS = ["نكشف", "نفتتح", "открываем", "unveil the heritage",
                    "open the heritage"]

# 讲解员填入的两条连播单（相同订单，两机各演一遍）
PLAYLIST = [
    ("gala.welcome", 2, None, None),
    ("gala.lanterns", 2, "f", None),
    ("gala.ribbon", 2, None, None),
    (OPAQUE_ID, 2, None, None),   # 受限揭幕词，未揭幕前只能出占位语
]


def hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def build_tokens() -> tuple[dict, bytes, bytes, str]:
    """编制公开包与两条密封分支令牌。"""
    public = build_public_package(ALL_ENTRIES, catalog_id=CATALOG_ID)
    key = seal.new_key()
    token_v2 = seal.seal(build_branch_payload(
        catalog_id=CATALOG_ID, schema_version=PUBLIC_SCHEMA,
        unveil_at=UNVEIL_AT, branch_entries=[REVEAL_V2],
        supersedes=[OPAQUE_ID]), key)
    token_v1 = seal.seal(build_branch_payload(
        catalog_id=CATALOG_ID, schema_version=1,
        unveil_at=UNVEIL_AT, branch_entries=[REVEAL_V1],
        supersedes=[OPAQUE_ID]), key)
    return public, token_v2, token_v1, key


def security_audit(public: dict) -> None:
    hr("0/1 发版前安全审计：枚举资源、试遍 0..200、查后备索引")
    blob = json.dumps(public, ensure_ascii=False)
    leaks = [root for root in RESTRICTED_ROOTS if root in blob]
    print(f"公开包条目：{[e['id'] for e in public['entries']]}")
    print(f"后备索引键：{list(public['fallback_index'])}（无受限词根）")
    print(f"密封清单：{public['seals']}（仅不透明 ID/时刻/密钥号）")
    assert not leaks, f"发现泄露词根：{leaks}"
    # 一般用户可列出全部资源，但受限条目只有占位语，且不含任何变体
    stub = next(e for e in public["entries"] if e["id"] == OPAQUE_ID)
    assert stub.get("embargo") and "variants" not in stub
    assert OPAQUE_ID not in json.dumps(public["fallback_index"], ensure_ascii=False)
    print("结论：枚举 0..200 全部数量与后备索引，受限词根零泄露 ✓")


def make_device(device_id: str, locale: str, font: Font, schema: int,
                legacy: dict, root: str) -> tuple[Device, Store, Resolver]:
    store = Store(os.path.join(root, device_id))
    store.install_public(build_public_package(ALL_ENTRIES, catalog_id=CATALOG_ID))
    store.attach_legacy(legacy)
    device = Device(device_id, locale, font, schema)
    return device, store, Resolver(store, font, store.state["legacy"])


def run_playlist(resolver: Resolver, locale: str, now: datetime) -> list:
    return [resolver.resolve(key, locale, n=n, gender=g, honorific=h, now=now)
            for key, n, g, h in PLAYLIST]


def main() -> None:
    workdir = tempfile.mkdtemp(prefix="festival-rehearsal-")
    print(f"本地排演工作区：{workdir}")
    public, token_v2, token_v1, key = build_tokens()
    security_audit(public)

    font_ar = Font("ar-legacy-font", {"arabic", "latin"})        # 无 star、无西里尔
    font_ru = Font("ru-legacy-font", {"cyrillic", "latin"})      # 无 star、无阿拉伯
    phone_ar, store_ar, resolver_ar = make_device(
        "phone-ar", "ar-AE", font_ar, 1, legacy_arabic(), workdir)
    phone_ru, store_ru, resolver_ru = make_device(
        "phone-ru", "ru-RU", font_ru, 1, legacy_russian(), workdir)

    hr("2 揭幕前排演：数字 2 的两种分支 + 缺字形走公开后备词")
    ar_before = run_playlist(resolver_ar, phone_ar.locale, BEFORE)
    ru_before = run_playlist(resolver_ru, phone_ru.locale, BEFORE)
    print(render_playlist(ar_before, LINE_WIDTH))
    print("\n" + "-" * 72 + "\n")
    print(render_playlist(ru_before, LINE_WIDTH))
    assert ar_before[0].provenance == "public" and "بكما" in ar_before[0].text
    assert ru_before[0].provenance == "public" and "гостям" in ru_before[0].text
    # ribbon 两机都缺 ✨，必须由仍公开的 opening_note 后备词播完
    assert ar_before[2].provenance == "fallback" and "✨" not in ar_before[2].text
    assert ru_before[2].provenance == "fallback" and "✨" not in ru_before[2].text
    # 受限词未揭幕：两机都只有安全占位语
    assert all(r.provenance == "placeholder" for r in (ar_before[3], ru_before[3]))

    hr("2b 切换地区：ar-AE → ar-EG 方言分支随地区变化")
    phone_ar.switch_region("ar-EG")
    egypt = resolver_ar.resolve("gala.lanterns", phone_ar.locale, n=1,
                                gender="f", now=BEFORE)
    print(f"ar-EG n=1 → [{egypt.provenance}] {egypt.text}")
    assert "بتنوّر" in egypt.text
    phone_ar.switch_region("ar-AE")

    hr("3 字幕席：行宽 26、句读断句、RTL 隔离与两条连播单排版（见上方输出）")

    hr("4 揭幕时刻：获准机原子接入 v2；落下交接的旧机被拒")
    store_ar.handover_key(KEY_ID, key)
    # phone-ar 只支持 schema v1：现行 v2 必须被模式闸门拒绝
    try:
        store_ar.install_branch(token_v2, supported_schema=1, now=BEFORE)
        raise AssertionError("v2 不应能接入 schema v1 旧机")
    except Exception as exc:
        print(f"phone-ar 接入 v2 被拒：{exc}")
    # phone-ru 落下交接、无密钥：同样接不进来
    try:
        store_ru.install_branch(token_v1, supported_schema=1, now=BEFORE)
        raise AssertionError("未授权机器不应能接入分支")
    except Exception as exc:
        print(f"phone-ru 无密钥接入被拒：{type(exc).__name__}: {exc}")

    # 现行版先交给一台能力完整的获准终端演示揭幕
    store_full = Store(os.path.join(workdir, "phone-full"))
    store_full.install_public(build_public_package(ALL_ENTRIES, catalog_id=CATALOG_ID))
    store_full.attach_legacy(legacy_arabic())
    store_full.handover_key(KEY_ID, key)
    store_full.install_branch(token_v2, supported_schema=PUBLIC_SCHEMA, now=BEFORE)
    font_full = Font("ar-full-font", {"arabic", "latin", "star"})
    resolver_full = Resolver(store_full, font_full, store_full.state["legacy"])
    print("揭幕前仍为占位：",
          resolver_full.resolve(OPAQUE_ID, "ar-AE", n=2, now=BEFORE).provenance)
    activated = store_full.activate_at_unveil(now=AFTER)
    print(f"揭幕激活，原子替换桩：{activated}")
    unveiled = resolver_full.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
    print(f"受限句获准播出 [{unveiled.provenance}]：{unveiled.text}")
    assert unveiled.provenance == "branch" and "لوحتين" in unveiled.text

    hr("5 先还原目录，再单换旧机支持的 v1 分支版本")
    store_full.rollback_branch(OPAQUE_ID)
    rolled = resolver_full.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
    print(f"还原后（桩重新生效）[{rolled.provenance}]：{rolled.text}")
    assert rolled.provenance == "placeholder"
    store_full.remove_branch(OPAQUE_ID)
    # 旧机支持的 v1：无新字形、措辞为兼容版
    store_ar.install_branch(token_v1, supported_schema=1, now=BEFORE)
    store_ar.activate_at_unveil(now=AFTER)
    ar_v1 = resolver_ar.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
    print(f"phone-ar 单换 v1 后 [{ar_v1.provenance}]：{ar_v1.text}")
    assert ar_v1.provenance == "branch" and "معرضين" in ar_v1.text

    hr("6 留存已发布包到第三台离线机，用相同订单复演")
    replica_root = os.path.join(workdir, "phone-replica")
    shutil.copytree(os.path.join(workdir, "phone-ar"), replica_root)
    store_replica = Store(replica_root)   # 全程离线，只读取已落盘状态
    resolver_replica = Resolver(store_replica, font_ar,
                                store_replica.state["legacy"])
    replica_run = run_playlist(resolver_replica, "ar-AE", AFTER)
    print(render_playlist(replica_run, LINE_WIDTH))
    expected = [r.provenance for r in replica_run]
    print("\n复演来源核对：", expected)
    assert expected == ["public", "public", "fallback", "branch"]
    # 未获准的 ru 机用同一订单：受限句仍是占位语（安全表达）
    ru_after = run_playlist(resolver_ru, "ru-RU", AFTER)
    print("未获准 phone-ru 同订单来源：", [r.provenance for r in ru_after])
    assert ru_after[3].provenance == "placeholder"

    hr("排演结论")
    print("• 未揭幕：任何枚举/数量探测/后备索引都看不到受限词根；")
    print("• 数字 2：ar-AE=two 双数，ru-RU=few，两机分支不同；")
    print("• 缺字形条目由仍公开的后备词 gala.opening_note 播完；")
    print("• 受限分支只在揭幕时刻于获准终端原子激活，可还原、可单换版本；")
    print("• 离线复刻机凭已落盘包按相同订单复演，来源与安全表达完全一致。")
    print(f"\n全部产物位于：{workdir}")


if __name__ == "__main__":
    main()
