# -*- coding: utf-8 -*-
"""合成文案（教学用，全部为虚构短语）。

覆盖题目要求的全部维度：
  地区（ar-AE / ar-EG、ru-RU）、数量复数、语法性别、敬语、
  跨目录后备、字形差异、受限揭幕词与安全占位语。
"""

from __future__ import annotations

from .catalog import LEGACY_SCHEMA, PUBLIC_SCHEMA

CATALOG_ID = "festival-2026-11-28"
KEY_ID = "key-curtain-a"
UNVEIL_AT = "2026-11-28T10:00:00+04:00"   # 周六场次揭幕时刻
OPAQUE_ID = "res-7f3a91c2"                # 不透明标识，不含词根

# ---------------------------------------------------------------- 公开条目
WELCOME = {
    "id": "gala.welcome",
    "variants": [
        {"locales": ["ar"], "plural": "one", "rtl": True,
         "text": "أهلاً بك في قاعة الثقافة"},
        {"locales": ["ar"], "plural": "two", "rtl": True,
         "text": "أهلاً بكما في قاعة الثقافة"},           # 双数
        {"locales": ["ar"], "plural": "few", "rtl": True,
         "text": "أهلاً بكم {n} في قاعة الثقافة"},
        {"locales": ["ar"], "plural": "many", "rtl": True,
         "text": "أهلاً بجميع الحاضرين في قاعة الثقافة"},
        {"locales": ["ru"], "plural": "one",
         "text": "Добро пожаловать в зал культуры"},
        {"locales": ["ru"], "plural": "few",
         "text": "Добро пожаловать {n} гостям в зал культуры"},
        {"locales": ["ru"], "plural": "many",
         "text": "Добро пожаловать всем гостям зала культуры"},
        {"locales": ["en"], "plural": "other",
         "text": "Welcome to the culture hall"},
    ],
}

LANTERNS = {
    "id": "gala.lanterns",
    "variants": [
        {"locales": ["ar-AE"], "plural": "one", "gender": "f", "rtl": True,
         "text": "فانوسية واحدة تضيء المدخل"},
        {"locales": ["ar-EG"], "plural": "one", "gender": "f", "rtl": True,
         "text": "فانوسة واحدة بتنوّر المدخل"},          # 地区方言分支
        {"locales": ["ar"], "plural": "two", "rtl": True,
         "text": "فانوسان يضيئان المدخل"},
        {"locales": ["ar"], "plural": "few", "rtl": True,
         "text": "{n} فوانيس تضيء المدخل"},
        {"locales": ["ar"], "plural": "many", "rtl": True,
         "text": "كثير من الفوانيس يضيء المدخل"},
        {"locales": ["ru"], "plural": "one", "gender": "f",
         "text": "Одна лампа освещает вход"},
        {"locales": ["ru"], "plural": "few", "gender": "f",
         "text": "{n} лампы освещают вход"},
        {"locales": ["ru"], "plural": "many",
         "text": "Много ламп освещает вход"},
        {"locales": ["en"], "plural": "other",
         "text": "{n} lanterns light the entrance"},
    ],
}

RIBBON = {
    "id": "gala.ribbon",
    "variants": [
        {"locales": ["ar"], "plural": "one", "rtl": True,
         "text": "شريط حريري واحد ✨ ينتظر الافتتاح"},     # 含两机都缺的 ✨
        {"locales": ["ar"], "plural": "two", "rtl": True,
         "text": "شريطان حريريان ✨ ينتظران الافتتاح"},
        {"locales": ["ar"], "plural": "few", "rtl": True,
         "text": "{n} أشرطة حريرية ✨ تنتظر الافتتاح"},
        {"locales": ["ar"], "plural": "many", "rtl": True,
         "text": "أشرطة حريرية كثيرة ✨ تنتظر الافتتاح"},
        {"locales": ["ru"], "plural": "one",
         "text": "Одна шёлковая лента ✨ ждёт открытия"},
        {"locales": ["ru"], "plural": "few",
         "text": "{n} шёлковые ленты ✨ ждут открытия"},
        {"locales": ["ru"], "plural": "many",
         "text": "Много шёлковых лент ✨ ждут открытия"},
        {"locales": ["en"], "plural": "other",
         "text": "{n} silk ribbons await the opening"},
    ],
    # 该条目自身渲染不了时，公开目录内的安全后备（不含特殊字形）
    "fallback": "gala.opening_note",
}

OPENING_NOTE = {
    "id": "gala.opening_note",
    "variants": [
        {"locales": ["ar"], "rtl": True,
         "text": "قاعة الثقافة ترحّب بكم قبل الافتتاح"},
        {"locales": ["ru"],
         "text": "Зал культуры приветствует вас до открытия"},
        {"locales": ["en"],
         "text": "The culture hall welcomes you before opening"},
    ],
}

GREETING = {
    "id": "gala.greeting",
    "variants": [
        {"locales": ["ar"], "plural": "one", "honorific": "formal",
         "rtl": True,
         "text": "حضرة الضيف الكريم، نرحّب بك رسمياً"},
        {"locales": ["ar"], "plural": "one", "honorific": "casual",
         "rtl": True,
         "text": "أهلاً وسهلاً يا صديقي"},
        {"locales": ["ru"], "plural": "one", "honorific": "formal",
         "text": "Уважаемый гость, рады приветствовать вас"},
        {"locales": ["ru"], "plural": "one", "honorific": "casual",
         "text": "Привет! Рады тебя видеть"},
        {"locales": ["en"], "plural": "one",
         "text": "Dear guest, welcome"},
    ],
}

# ------------------------------------------------ 受限条目（公开包中只有桩）
REVEAL_EMBARGO = {
    "id": "gala.reveal",
    "embargo": {
        "opaque_id": OPAQUE_ID,
        "unveil_at": UNVEIL_AT,
        "key_id": KEY_ID,
    },
    "safe_placeholders": [
        {"locales": ["ar"], "rtl": True,
         "text": "سيُعلن عن هذه الجملة في حفل الافتتاح"},
        {"locales": ["ru"],
         "text": "Эта фраза будет объявлена на церемонии открытия"},
        {"locales": ["en"],
         "text": "This line will be announced at the opening"},
    ],
}

REVEAL_V2 = {
    "id": "gala.reveal",
    "variants": [
        {"locales": ["ar"], "plural": "one", "rtl": True,
         "text": "نكشف الستار اليوم عن لوحة التراث ✨"},
        {"locales": ["ar"], "plural": "two", "rtl": True,
         "text": "نكشف الستار اليوم عن لوحتين تراثيتين ✨"},
        {"locales": ["ar"], "plural": "few", "rtl": True,
         "text": "نكشف الستار اليوم عن {n} لوحات تراثية ✨"},
        {"locales": ["ar"], "plural": "many", "rtl": True,
         "text": "نكشف الستار اليوم عن إرثنا المشترك ✨"},
        {"locales": ["ru"], "plural": "one",
         "text": "Сегодня мы открываем наследную роспись ✨"},
        {"locales": ["ru"], "plural": "few",
         "text": "Сегодня мы открываем {n} наследные росписи ✨"},
        {"locales": ["ru"], "plural": "many",
         "text": "Сегодня мы открываем наше общее наследие ✨"},
        {"locales": ["en"], "plural": "other",
         "text": "Today we unveil the heritage mural"},
    ],
    "fallback": "gala.opening_note",
}

# 旧机只支持 schema v1：同一受限词根的兼容分支，措辞不同、无新字形
REVEAL_V1 = {
    "id": "gala.reveal",
    "variants": [
        {"locales": ["ar"], "plural": "one", "rtl": True,
         "text": "نفتتح اليوم معرض التراث بحضوركم"},
        {"locales": ["ar"], "plural": "two", "rtl": True,
         "text": "نفتتح اليوم معرضين تراثيين بحضوركم"},
        {"locales": ["ar"], "plural": "few", "rtl": True,
         "text": "نفتتح اليوم {n} معارض تراثية بحضوركم"},
        {"locales": ["ar"], "plural": "many", "rtl": True,
         "text": "نفتتح اليوم إرثنا المشترك بحضوركم"},
        {"locales": ["ru"], "plural": "one",
         "text": "Сегодня мы открываем наследную выставку"},
        {"locales": ["ru"], "plural": "few",
         "text": "Сегодня мы открываем {n} наследные выставки"},
        {"locales": ["ru"], "plural": "many",
         "text": "Сегодня мы открываем наше общее наследие"},
        {"locales": ["en"], "plural": "other",
         "text": "Today we open the heritage exhibition"},
    ],
    "fallback": "gala.opening_note",
}

ALL_ENTRIES = [WELCOME, LANTERNS, RIBBON, OPENING_NOTE, GREETING, REVEAL_EMBARGO]

# ------------------------------------------------ 两台旧机各自的旧版后备目录
def legacy_arabic() -> dict:
    return {
        "schema_version": LEGACY_SCHEMA,
        "kind": "legacy",
        "catalog_id": "legacy-ar-2025",
        "entries": [
            {"id": "gala.welcome", "variants": [
                {"locales": ["ar"], "rtl": True,
                 "text": "مرحباً بكم في القاعة (نسخة احتياطية قديمة)"},
                {"locales": ["en"],
                 "text": "Welcome (old backup copy)"}]},
            # 仍公开的后备词：ribbon 缺字形时靠它播完
            {"id": "gala.opening_note", "variants": [
                {"locales": ["ar"], "rtl": True,
                 "text": "القاعة مفتوحة، تفضّلوا بالدخول"},
                {"locales": ["en"],
                 "text": "The hall is open, please come in"}]},
            # 安全性质：旧目录也不收录受限词根 gala.reveal，
            # 未交接的旧机无法借旧后备识别未公开句子。
        ],
        "fallback_index": {"gala.ribbon": "gala.opening_note"},
    }


def legacy_russian() -> dict:
    return {
        "schema_version": LEGACY_SCHEMA,
        "kind": "legacy",
        "catalog_id": "legacy-ru-2025",
        "entries": [
            {"id": "gala.welcome", "variants": [
                {"locales": ["ru"],
                 "text": "Добро пожаловать (старая резервная копия)"},
                {"locales": ["en"],
                 "text": "Welcome (old backup copy)"}]},
            {"id": "gala.opening_note", "variants": [
                {"locales": ["ru"],
                 "text": "Зал открыт, проходите, пожалуйста"},
                {"locales": ["en"],
                 "text": "The hall is open, please come in"}]},
            # 注意：俄机旧目录不含 gala.reveal，未获准交接时只能用安全占位/安全行
        ],
        "fallback_index": {"gala.ribbon": "gala.opening_note"},
    }
