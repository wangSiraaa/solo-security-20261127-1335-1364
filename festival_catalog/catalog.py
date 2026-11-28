# -*- coding: utf-8 -*-
"""目录编制：公开包、受限分支包与变体选择规则。

安全约定（路线 B）：
  1. 受限条目在公开包中只保留不透明 ID、揭幕时刻与安全占位语，
     词根、变体数量一律不进入公开材料；
  2. 后备索引只收录公开词根，受限条目不占索引键、也不作为索引值，
     杜绝“借后备索引识别词根”；
  3. 受限分支单独编制为 branch 载荷，经 seal 密封后交接，
     获准终端在揭幕时刻原子接入。
"""

from __future__ import annotations

import copy

from .plurals import category, locale_chain

PUBLIC_SCHEMA = 2  # 当前发布目录的模式版本
LEGACY_SCHEMA = 1  # 两台旧机各自随机后备目录的模式版本


class CatalogError(Exception):
    pass


def rank_variants(variants: list[dict], locale: str, n: int,
                  gender: str | None = None,
                  honorific: str | None = None) -> list[dict]:
    """按匹配度从高到低返回可用变体（同分时保留声明顺序）。"""
    chain = locale_chain(locale)
    cat = category(locale, n)
    ranked = []
    for index, variant in enumerate(variants):
        score = 0
        ok = True
        if "locales" in variant:
            hit = next((token for token in chain if token in variant["locales"]), None)
            if hit is None:
                ok = False
            else:
                score += 10 if hit == chain[0] else 5
        if "plural" in variant:
            if variant["plural"] != cat:
                ok = False
            else:
                score += 8
        if gender is not None and "gender" in variant:
            if variant["gender"] != gender:
                ok = False
            else:
                score += 2
        if honorific is not None and "honorific" in variant:
            if variant["honorific"] != honorific:
                ok = False
            else:
                score += 2
        if ok:
            ranked.append((score, index, variant))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [variant for _, _, variant in ranked]


def select_variant(variants: list[dict], locale: str, n: int,
                   gender: str | None = None,
                   honorific: str | None = None) -> dict | None:
    """按 locale 链、复数类别、语法性别、敬语过滤，取打分最高的变体。"""
    ranked = rank_variants(variants, locale, n, gender, honorific)
    return ranked[0] if ranked else None


def render_variant(variant: dict, n: int) -> str:
    """把讲解员填入的数量代入变体文本。"""
    return variant["text"].replace("{n}", str(n))


def build_public_package(entries: list[dict], *, catalog_id: str,
                         default_locale: str = "en") -> dict:
    """把全部条目编制成可广泛分发的公开包（受限条目降为不透明桩）。"""
    public_entries = []
    fallback_index = {}
    seals = {}
    for entry in entries:
        if entry.get("embargo"):
            embargo = entry["embargo"]
            # 只保留占位语与不透明标识；不带词根、不带变体清单
            public_entries.append({
                "id": embargo["opaque_id"],
                "embargo": True,
                "unveil_at": embargo["unveil_at"],
                "safe_placeholders": copy.deepcopy(entry["safe_placeholders"]),
            })
            seals[embargo["opaque_id"]] = {
                "key_id": embargo["key_id"],
                "unveil_at": embargo["unveil_at"],
            }
            continue
        public_entries.append(copy.deepcopy(entry))
        if "fallback" in entry:
            # 后备索引仅收录公开词根
            fallback_index[entry["id"]] = entry["fallback"]
    return {
        "schema_version": PUBLIC_SCHEMA,
        "kind": "public",
        "catalog_id": catalog_id,
        "default_locale": default_locale,
        "entries": public_entries,
        "fallback_index": fallback_index,
        "seals": seals,
    }


def build_branch_payload(*, catalog_id: str, schema_version: int,
                         unveil_at: str, branch_entries: list[dict],
                         supersedes: list[str]) -> dict:
    """编制受限分支载荷（随后由 seal.seal 密封交接）。

    supersedes 与 branch_entries 按序一一对应，给出不透明桩到真实条目的
    替换关系；客户端据此在揭幕时原子替换，并在还原时找回桩。
    """
    if len(supersedes) != len(branch_entries):
        raise CatalogError("supersedes 与 branch_entries 必须一一对应")
    replacements = [{"opaque": opaque, "entry_id": entry["id"]}
                    for opaque, entry in zip(supersedes, branch_entries)]
    return {
        "kind": "branch",
        "schema_version": schema_version,
        "catalog_id": catalog_id,
        "unveil_at": unveil_at,
        "supersedes": list(supersedes),
        "replacements": replacements,
        "entries": copy.deepcopy(branch_entries),
    }


def index_entries(entries: list[dict]) -> dict[str, dict]:
    return {entry["id"]: entry for entry in entries}
