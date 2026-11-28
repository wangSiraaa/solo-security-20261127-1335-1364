# -*- coding: utf-8 -*-
"""单元测试：复数规则、未揭幕安全性质、客户端解析与字幕排版。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from festival_catalog import seal
from festival_catalog.catalog import (PUBLIC_SCHEMA, CatalogError,
                                      build_branch_payload,
                                      build_public_package, select_variant)
from festival_catalog.client import SAFE_LINE, Resolver, Store
from festival_catalog.devices import Device, Font
from festival_catalog.demo_data import (ALL_ENTRIES, CATALOG_ID, KEY_ID,
                                        OPAQUE_ID, REVEAL_V1, REVEAL_V2,
                                        UNVEIL_AT, legacy_arabic,
                                        legacy_russian)
from festival_catalog.plurals import category, locale_chain
from festival_catalog.subtitles import format_line, render_playlist, wrap_width

UNVEIL = datetime.fromisoformat(UNVEIL_AT)
BEFORE = UNVEIL - timedelta(hours=2)
AFTER = UNVEIL + timedelta(minutes=5)

# 受限台词词根，公开材料中任何位置都不得出现
RESTRICTED_ROOTS = ["نكشف", "نفتتح", "открываем роспись", "открываем наследную выставку",
                    "unveil the heritage", "open the heritage"]
PROBE_NUMBERS = list(range(0, 201))


class PluralTests(unittest.TestCase):
    def test_two_forks_differently_on_old_phones(self):
        # 题目核心：数字二在两种地区走不同分支
        self.assertEqual(category("ar-AE", 2), "two")
        self.assertEqual(category("ru-RU", 2), "few")

    def test_arabic_full_rule(self):
        self.assertEqual(category("ar", 0), "zero")
        self.assertEqual(category("ar", 1), "one")
        self.assertEqual(category("ar", 3), "few")
        self.assertEqual(category("ar", 11), "many")
        self.assertEqual(category("ar", 200), "other")

    def test_russian_full_rule(self):
        self.assertEqual(category("ru", 1), "one")
        self.assertEqual(category("ru", 2), "few")
        self.assertEqual(category("ru", 12), "many")
        self.assertEqual(category("ru", 22), "few")
        self.assertEqual(category("ru", 100), "many")

    def test_locale_chain(self):
        self.assertEqual(locale_chain("ar-AE"), ["ar-AE", "ar", "en"])
        self.assertEqual(locale_chain("ru"), ["ru", "en"])


class PublicPackageSecurityTests(unittest.TestCase):
    def setUp(self):
        self.pkg = build_public_package(ALL_ENTRIES, catalog_id=CATALOG_ID)

    def test_restricted_entry_is_opaque_stub(self):
        entry = next(e for e in self.pkg["entries"] if e["id"] == OPAQUE_ID)
        self.assertTrue(entry["embargo"])
        self.assertNotIn("variants", entry)
        self.assertNotIn("gala.reveal", str(entry))

    def test_no_restricted_root_anywhere_even_probing_all_quantities(self):
        blob = repr(self.pkg)
        for root in RESTRICTED_ROOTS:
            self.assertNotIn(root, blob)
        # 试遍允许的数量：桩上没有任何复数变体可选
        stub = next(e for e in self.pkg["entries"] if e["id"] == OPAQUE_ID)
        for n in PROBE_NUMBERS:
            for locale in ("ar-AE", "ar-EG", "ru-RU", "en"):
                self.assertIsNone(select_variant(
                    stub.get("variants", []), locale, n))

    def test_fallback_index_carries_only_public_roots(self):
        for source, target in self.pkg["fallback_index"].items():
            self.assertNotIn(OPAQUE_ID, source)
            self.assertNotIn(OPAQUE_ID, target)
        self.assertNotIn(OPAQUE_ID, repr(self.pkg["fallback_index"]))
        self.assertNotIn("gala.reveal", repr(self.pkg["fallback_index"]))

    def test_seals_meta_has_no_text(self):
        meta = self.pkg["seals"][OPAQUE_ID]
        self.assertEqual(set(meta.keys()), {"key_id", "unveil_at"})


class SealedBranchTests(unittest.TestCase):
    def test_seal_roundtrip_and_tamper(self):
        key = seal.new_key()
        payload = build_branch_payload(
            catalog_id=CATALOG_ID, schema_version=PUBLIC_SCHEMA,
            unveil_at=UNVEIL_AT, branch_entries=[REVEAL_V2],
            supersedes=[OPAQUE_ID])
        token = seal.seal(payload, key)
        self.assertEqual(seal.unseal(token, key)["entries"][0]["id"], "gala.reveal")
        wrong = seal.new_key()
        with self.assertRaises(seal.UnsealError):
            seal.unseal(token, wrong)
        tampered = bytearray(token)
        tampered[-1] ^= 0x01
        with self.assertRaises(seal.UnsealError):
            seal.unseal(bytes(tampered), key)


def _make_store(legacy):
    tmp = tempfile.mkdtemp()
    store = Store(os.path.join(tmp, "state"))
    store.install_public(build_public_package(ALL_ENTRIES, catalog_id=CATALOG_ID))
    store.attach_legacy(legacy)
    return store


class ResolverSecurityTests(unittest.TestCase):
    def test_before_unveil_only_placeholder_any_quantity(self):
        store = _make_store(legacy_arabic())
        resolver = Resolver(store, Font("ar-font", {"arabic", "latin"}))
        for n in PROBE_NUMBERS:
            result = resolver.resolve(OPAQUE_ID, "ar-AE", n=n, now=BEFORE)
            self.assertEqual(result.provenance, "placeholder")
            for root in RESTRICTED_ROOTS:
                self.assertNotIn(root, result.text)

    def test_unauthorized_device_never_receives_branch(self):
        store = _make_store(legacy_russian())
        token = seal.seal(build_branch_payload(
            catalog_id=CATALOG_ID, schema_version=PUBLIC_SCHEMA,
            unveil_at=UNVEIL_AT, branch_entries=[REVEAL_V2],
            supersedes=[OPAQUE_ID]), seal.new_key())
        # 落下交接的旧机没有任何密钥：接入在匹配阶段被拒
        with self.assertRaises(CatalogError):
            store.install_branch(token, supported_schema=PUBLIC_SCHEMA, now=BEFORE)
        # 即便持有一把无关密钥，启封也必须失败
        store.handover_key(KEY_ID, seal.new_key())
        with self.assertRaises(seal.UnsealError):
            store.install_branch(token, supported_schema=PUBLIC_SCHEMA, now=BEFORE)
        resolver = Resolver(store, Font("ru-font", {"cyrillic", "latin"}))
        result = resolver.resolve(OPAQUE_ID, "ru-RU", n=2, now=AFTER)
        self.assertEqual(result.provenance, "placeholder")

    def test_legacy_catalog_cannot_reveal_restricted_root_on_russian_phone(self):
        store = _make_store(legacy_russian())  # 旧目录刻意不含 reveal
        resolver = Resolver(store, Font("ru-font", {"cyrillic", "latin"}))
        result = resolver.resolve(OPAQUE_ID, "ru-RU", n=5, now=BEFORE)
        self.assertEqual(result.provenance, "placeholder")
        self.assertNotIn("открываем", result.text)


class ResolverPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.store = _make_store(legacy_arabic())
        self.key = seal.new_key()
        self.store.handover_key(KEY_ID, self.key)
        self.font_ar = Font("ar-font", {"arabic", "latin"})
        self.font_ru = Font("ru-font", {"cyrillic", "latin"})

    def _stage(self, entry, schema):
        token = seal.seal(build_branch_payload(
            catalog_id=CATALOG_ID, schema_version=schema,
            unveil_at=UNVEIL_AT, branch_entries=[entry],
            supersedes=[OPAQUE_ID]), self.key)
        self.store.install_branch(token, supported_schema=schema, now=BEFORE)

    def test_arabic_dual_branch_at_two(self):
        resolver = Resolver(self.store, self.font_ar)
        result = resolver.resolve("gala.welcome", "ar-AE", n=2, now=BEFORE)
        self.assertIn("بكما", result.text)
        self.assertTrue(result.rtl)

    def test_russian_few_branch_at_two(self):
        store = _make_store(legacy_russian())
        resolver = Resolver(store, self.font_ru)
        result = resolver.resolve("gala.lanterns", "ru-RU", n=2, now=BEFORE)
        self.assertIn("2 лампы", result.text)

    def test_missing_glyph_falls_back_to_still_public_word(self):
        # ar 机与 ru 机都没有 star 字形：ribbon 必须靠公开后备词播完
        resolver = Resolver(self.store, self.font_ar)
        result = resolver.resolve("gala.ribbon", "ar-AE", n=2, now=BEFORE)
        self.assertNotIn("✨", result.text)
        self.assertEqual(result.provenance, "fallback")
        store_ru = _make_store(legacy_russian())
        resolver_ru = Resolver(store_ru, self.font_ru)
        result_ru = resolver_ru.resolve("gala.ribbon", "ru-RU", n=2, now=BEFORE)
        self.assertNotIn("✨", result_ru.text)
        self.assertIn("приветствует", result_ru.text)

    def test_gender_and_honorific_branches(self):
        resolver = Resolver(self.store, self.font_ar)
        formal = resolver.resolve("gala.greeting", "ar-AE", n=1,
                                  honorific="formal", now=BEFORE)
        casual = resolver.resolve("gala.greeting", "ar-AE", n=1,
                                  honorific="casual", now=BEFORE)
        self.assertIn("الكريم", formal.text)
        self.assertIn("صديقي", casual.text)
        lantern = resolver.resolve("gala.lanterns", "ar-AE", n=1,
                                   gender="f", now=BEFORE)
        self.assertIn("فانوسية", lantern.text)

    def test_region_switch_chooses_dialect_variant(self):
        device = Device("phone-ar", "ar-AE", self.font_ar, PUBLIC_SCHEMA)
        resolver = Resolver(self.store, self.font_ar)
        uae = resolver.resolve("gala.lanterns", device.locale, n=1, now=BEFORE)
        device.switch_region("ar-EG")
        egypt = resolver.resolve("gala.lanterns", device.locale, n=1, now=BEFORE)
        self.assertIn("تضيء", uae.text)
        self.assertIn("بتنوّر", egypt.text)

    def test_atomic_activate_and_rollback_restores_placeholder(self):
        self._stage(REVEAL_V2, PUBLIC_SCHEMA)
        # v2 分支带 ✨：本机字体具备该字形时直接播受限原句
        full_font = Font("ar-full", {"arabic", "latin", "star"})
        resolver = Resolver(self.store, full_font)
        self.assertEqual(
            resolver.resolve(OPAQUE_ID, "ar-AE", n=2, now=BEFORE).provenance,
            "placeholder")
        self.store.activate_at_unveil(now=AFTER)
        unveiled = resolver.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
        self.assertEqual(unveiled.provenance, "branch")
        self.assertIn("لوحتين", unveiled.text)
        # 还原目录：桩重新生效
        self.store.rollback_branch(OPAQUE_ID)
        again = resolver.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
        self.assertEqual(again.provenance, "placeholder")

    def test_schema_gate_old_phone_rejects_v2_then_accepts_v1(self):
        v2_token = seal.seal(build_branch_payload(
            catalog_id=CATALOG_ID, schema_version=PUBLIC_SCHEMA,
            unveil_at=UNVEIL_AT, branch_entries=[REVEAL_V2],
            supersedes=[OPAQUE_ID]), self.key)
        with self.assertRaises(Exception):
            self.store.install_branch(v2_token, supported_schema=1, now=BEFORE)
        self._stage(REVEAL_V1, 1)
        self.store.activate_at_unveil(now=AFTER)
        font_legacy = Font("ar-legacy", {"arabic", "latin"})
        resolver = Resolver(self.store, font_legacy)
        result = resolver.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
        self.assertEqual(result.provenance, "branch")
        self.assertIn("معرضين", result.text)

    def test_unveiled_branch_with_unsupported_glyph_uses_public_fallback(self):
        # v2 分支带 ✨，若字体缺该字形，仍须能用公开后备词播完
        self._stage(REVEAL_V2, PUBLIC_SCHEMA)
        self.store.activate_at_unveil(now=AFTER)
        font_no_star = Font("ar-no-star", {"arabic", "latin"})
        resolver = Resolver(self.store, font_no_star)
        result = resolver.resolve(OPAQUE_ID, "ar-AE", n=2, now=AFTER)
        self.assertNotIn("✨", result.text)
        self.assertEqual(result.provenance, "fallback")

    def test_safe_line_when_nothing_renders(self):
        store = _make_store(legacy_russian())
        tiny_font = Font("latin-only", {"latin"})
        # 显式空后备目录：西里尔文本全部渲染不了、旧目录也无表达，才到通用安全行
        empty_legacy = {"schema_version": 1, "kind": "legacy",
                        "catalog_id": "empty", "entries": [], "fallback_index": {}}
        resolver = Resolver(store, tiny_font, legacy_catalog=empty_legacy)
        result = resolver.resolve("gala.unknown_key", "ru-RU", n=2, now=BEFORE)
        self.assertEqual(result.text, SAFE_LINE)
        self.assertEqual(result.provenance, "safe")


class SubtitleTests(unittest.TestCase):
    def test_wrap_and_rtl_isolation(self):
        store = _make_store(legacy_arabic())
        resolver = Resolver(store, Font("ar-font", {"arabic", "latin"}))
        result = resolver.resolve("gala.welcome", "ar-AE", n=3, now=BEFORE)
        lines = format_line(result, width=20)
        self.assertTrue(all(line.startswith("⁧") and line.endswith("⁦")
                            for line in lines))

    def test_playlist_renders_two_connected_sheets(self):
        store = _make_store(legacy_arabic())
        resolver = Resolver(store, Font("ar-font", {"arabic", "latin"}))
        results = [resolver.resolve("gala.welcome", "ar-AE", n=2, now=BEFORE),
                   resolver.resolve("gala.ribbon", "ar-AE", n=2, now=BEFORE)]
        out = render_playlist(results, width=24)
        self.assertEqual(out.count("#1"), 1)
        self.assertEqual(out.count("#2"), 1)
        self.assertIn("[public]", out)
        self.assertIn("[fallback]", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
