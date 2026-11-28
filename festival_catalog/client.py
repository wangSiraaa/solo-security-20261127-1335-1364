# -*- coding: utf-8 -*-
"""客户端：本地目录解释器、原子接入存储与安全排演。

解析顺序（每一步都记录 provenance，便于字幕席核对“哪句获准、哪句改安全表达”）：
  已揭幕分支 → 公开包公开条目 → 公开后备索引（仅公开词根）→
  同机旧版后备目录 → 通用安全行。
受限条目在未揭幕或未接入时只返回占位语；后备索引里没有受限词根。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import seal
from .catalog import (LEGACY_SCHEMA, CatalogError, index_entries,
                      rank_variants, render_variant, select_variant)
from .devices import Font

SAFE_LINE = "[SAFE LINE]"  # 通用安全表达：纯 ASCII，任何字体都能渲染


@dataclass
class ResolveResult:
    text: str
    provenance: str                       # branch/public/fallback/legacy/safe/placeholder
    key: str
    locale: str
    n: int | None
    rtl: bool = False
    trace: list[str] = field(default_factory=list)


class Store:
    """终端本地存储：公开包、旧版后备目录与分支目录的原子管理。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._path = os.path.join(root, "state.json")
        self.state = {"public": None, "legacy": None,
                      "branches": {}, "keys": {}}
        if os.path.exists(self._path):
            with open(self._path, encoding="utf-8") as handle:
                self.state = json.load(handle)

    def _flush(self) -> None:
        # 原子落盘：同目录临时文件 + os.replace，崩溃也只留新旧完整版本之一
        directory = os.path.dirname(self._path)
        fd, tmp = tempfile.mkstemp(prefix=".state-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            os.unlink(tmp)
            raise

    def install_public(self, package: dict) -> None:
        if package.get("kind") != "public":
            raise CatalogError("不是公开包")
        self.state["public"] = package
        self._flush()

    def attach_legacy(self, legacy: dict) -> None:
        if legacy.get("schema_version") != LEGACY_SCHEMA:
            raise CatalogError("旧版后备目录模式版本不符")
        self.state["legacy"] = legacy
        self._flush()

    def handover_key(self, key_id: str, key: str) -> None:
        """讲解员把获准终端的密钥交给本机（未获准机器收不到）。"""
        self.state["keys"][key_id] = key
        self._flush()

    def has_key(self, key_id: str) -> bool:
        return key_id in self.state["keys"]

    def install_branch(self, token: bytes, *, supported_schema: int,
                       now: datetime) -> None:
        """原子接入受限分支；任何核验失败都保持原状态不变。"""
        package = self.state.get("public")
        if package is None:
            raise CatalogError("公开包尚未安装")
        meta = None
        payload = None
        matched = False
        # 先全量核验，再动状态；未获准终端无密钥，启封异常原样上抛
        for opaque_id, info in package.get("seals", {}).items():
            if not self.has_key(info["key_id"]):
                continue
            candidate = seal.unseal(token, self.state["keys"][info["key_id"]])
            if opaque_id in candidate.get("supersedes", []):
                matched = True
                meta, payload = info, candidate
                break
        if not matched:
            raise CatalogError("分支与本机密钥或目录不匹配，拒绝接入")
        if payload["schema_version"] > supported_schema:
            raise CatalogError(
                f"分支模式 v{payload['schema_version']} 超出本机能力 v{supported_schema}")
        if payload["catalog_id"] != package["catalog_id"]:
            raise CatalogError("分支目录来源不一致")
        if now >= datetime.fromisoformat(payload["unveil_at"]):
            raise CatalogError("已到/超过揭幕时刻的分支必须走揭幕流程接入")
        self.state["branches"][meta["key_id"] + ":" + payload["unveil_at"]] = payload
        self._flush()

    def activate_at_unveil(self, *, now: datetime) -> list[str]:
        """揭幕时刻：把已就位的分支原子激活（替换对应不透明桩）。"""
        activated = []
        for slot, payload in list(self.state["branches"].items()):
            if slot.endswith(":staged"):
                continue
            if now >= datetime.fromisoformat(payload["unveil_at"]):
                self.state["branches"][slot + ":active"] = payload
                del self.state["branches"][slot]
                activated.extend(payload["supersedes"])
        if activated:
            self._flush()
        return activated

    def active_branches(self) -> list[dict]:
        return [payload for slot, payload in self.state["branches"].items()
                if slot.endswith(":active")]

    def rollback_branch(self, opaque_id: str) -> None:
        """还原目录：撤下已激活分支，公开包中的桩重新生效。"""
        for slot, payload in list(self.state["branches"].items()):
            if slot.endswith(":active") and opaque_id in payload["supersedes"]:
                restored_slot = slot[: -len(":active")]
                self.state["branches"][restored_slot] = payload
                del self.state["branches"][slot]
                self._flush()
                return
        raise CatalogError("没有可还原的已激活分支")

    def remove_branch(self, opaque_id: str) -> None:
        """单换分支版本前，先移除旧版本（仍保持原子落盘）。"""
        for slot, payload in list(self.state["branches"].items()):
            if opaque_id in payload["supersedes"]:
                del self.state["branches"][slot]
        self._flush()


class Resolver:
    def __init__(self, store: Store, font: Font, legacy_catalog: dict | None = None):
        self.store = store
        self.font = font
        self.legacy_catalog = legacy_catalog or store.state.get("legacy")

    def _public_lookup(self, key: str) -> dict | None:
        package = self.store.state.get("public")
        if package is None:
            return None
        return index_entries(package["entries"]).get(key)

    def _branch_lookup(self, key: str, now: datetime) -> dict | None:
        """只在已揭幕激活的分支里查真实条目；未激活不暴露任何内容。"""
        for payload in self.store.active_branches():
            if now < datetime.fromisoformat(payload["unveil_at"]):
                continue
            entry = index_entries(payload["entries"]).get(key)
            if entry is not None:
                return entry
        return None

    def _branch_for_opaque(self, opaque_id: str, now: datetime) -> dict | None:
        for payload in self.store.active_branches():
            if now < datetime.fromisoformat(payload["unveil_at"]):
                continue
            if opaque_id in payload["supersedes"]:
                return payload
        return None

    def _placeholder(self, entry: dict, locale: str, n: int | None,
                     trace: list[str]) -> ResolveResult:
        # 占位语沿 locale 链逐级回退，挑本机能渲染的变体；
        # 整条链都渲染不了才升级为通用安全行。
        from .plurals import locale_chain
        for fallback_locale in locale_chain(locale):
            for candidate in (n or 1, 1):
                variant = select_variant(entry["safe_placeholders"],
                                         fallback_locale, candidate)
                if variant is None:
                    continue
                text = render_variant(variant, candidate)
                if self.font.can_render(text):
                    return ResolveResult(
                        text, "placeholder", entry["id"], locale, n,
                        variant.get("rtl", False),
                        trace + [f"受限条目未揭幕：返回安全占位语（{fallback_locale}）"])
        return self._safe_result(entry["id"], locale, n,
                                 trace + ["占位语也缺字形，升级通用安全行"])

    def resolve(self, key: str, locale: str, n: int = 1, *,
                gender: str | None = None, honorific: str | None = None,
                now: datetime | None = None,
                _depth: int = 0) -> ResolveResult:
        now = now or datetime.now(timezone.utc)
        trace: list[str] = []
        if _depth > 6:
            return self._safe_result(key, locale, n, trace + ["后备链过深"])

        # 0) 带目录前缀的显式后备（例如 legacy:gala.opening_note）
        if ":" in key and _depth == 0:
            catalog_name, bare = key.split(":", 1)
            if catalog_name == "legacy":
                return self._legacy(bare, locale, n, gender, honorific, now, trace)
            raise CatalogError(f"未知目录前缀 {catalog_name}")

        # 1) 受限不透明桩（公开包的唯一受限外观）
        stub = self._public_lookup(key)
        if stub is not None and stub.get("embargo"):
            payload = self._branch_for_opaque(key, now)
            if payload is None:
                # 未揭幕/未交接：只有占位语；后备索引不含受限词根，禁止借索引探测
                return self._placeholder(stub, locale, n, trace)
            real_id = next(item["entry_id"] for item in payload["replacements"]
                           if item["opaque"] == key)
            branch_entry = index_entries(payload["entries"])[real_id]
            result = self._render(branch_entry, locale, n, gender, honorific,
                                  "branch", trace + ["揭幕分支已原子替换受限桩"])
            if result:
                return result
            # 受限条目自身无可用变体（如缺字形）：只允许走其声明的公开后备
            return self._fallback_for(branch_entry, locale, n, gender,
                                      honorific, now, _depth,
                                      trace + ["受限分支无可用变体，转其公开后备"])

        # 2) 已揭幕分支中的真实词根（获准终端的别名解析）
        branch_entry = self._branch_lookup(key, now)
        if branch_entry is not None:
            result = self._render(branch_entry, locale, n, gender, honorific,
                                  "branch", trace + ["命中已揭幕受限分支"])
            if result:
                return result
            return self._fallback_for(branch_entry, locale, n, gender,
                                      honorific, now, _depth,
                                      trace + ["受限分支无可用变体，转其公开后备"])

        # 3) 公开包公开条目
        if stub is not None:
            result = self._render(stub, locale, n, gender, honorific, "public",
                                  trace + [f"命中公开条目 {key}"])
            if result:
                return result
            package = self.store.state.get("public") or {}
            fallback = package.get("fallback_index", {}).get(key)
            if fallback:
                return self._fallback_key(fallback, locale, n, gender,
                                          honorific, now, _depth,
                                          trace + [f"转公开后备 {fallback}"])

        # 4) 同机旧版后备目录
        legacy_result = self._legacy(key, locale, n, gender, honorific, now,
                                     trace + [f"公开目录无 {key}，转旧版后备"],
                                     _depth)
        if legacy_result.provenance != "safe":
            return legacy_result

        return self._safe_result(key, locale, n, trace + ["所有目录均无可用表达"])

    def _fallback_for(self, entry: dict, locale: str, n: int, gender: str | None,
                      honorific: str | None, now: datetime, depth: int,
                      trace: list[str]) -> ResolveResult:
        fallback = entry.get("fallback")
        if not fallback:
            return self._safe_result(entry["id"], locale, n,
                                     trace + ["无后备声明，启用通用安全行"])
        return self._fallback_key(fallback, locale, n, gender, honorific,
                                  now, depth, trace)

    def _fallback_key(self, fallback: str, locale: str, n: int,
                      gender: str | None, honorific: str | None,
                      now: datetime, depth: int,
                      trace: list[str]) -> ResolveResult:
        result = self.resolve(fallback, locale, n, gender=gender,
                              honorific=honorific, now=now, _depth=depth + 1)
        # 经后备链得到的非安全结果统一标注 fallback，便于字幕席核对
        if result.provenance != "safe":
            result.provenance = "fallback"
        result.trace = trace + result.trace
        return result

    def _legacy(self, key: str, locale: str, n: int, gender: str | None,
                honorific: str | None, now: datetime,
                trace: list[str], depth: int = 0) -> ResolveResult:
        if self.legacy_catalog is None:
            return self._safe_result(key, locale, n, trace + ["本机无旧版后备目录"])
        entry = index_entries(self.legacy_catalog["entries"]).get(key)
        if entry is None:
            return self._safe_result(key, locale, n, trace + [f"旧版后备无 {key}"])
        result = self._render(entry, locale, n, gender, honorific, "legacy",
                              trace + [f"命中旧版后备 {key}"])
        if result:
            return result
        fallback = self.legacy_catalog.get("fallback_index", {}).get(key)
        if fallback:
            return self._fallback_key(fallback, locale, n, gender, honorific,
                                      now, depth,
                                      trace + [f"旧版后备再回退 {fallback}"])
        return self._safe_result(key, locale, n, trace + ["旧版后备无可用变体"])

    def _render(self, entry: dict, locale: str, n: int, gender: str | None,
                honorific: str | None, provenance: str,
                trace: list[str]) -> ResolveResult | None:
        # 同一目录内先按匹配度排序，再跳过本机字体渲染不了的变体，
        # 全部不可渲染才返回 None，交回上层走后备目录。
        for variant in rank_variants(entry["variants"], locale, n,
                                     gender, honorific):
            text = render_variant(variant, n)
            if not self.font.can_render(text):
                trace.append(f"字体缺字形（{self.font.name}），试下一变体")
                continue
            return ResolveResult(text, provenance, entry["id"], locale, n,
                                 variant.get("rtl", False), trace)
        return None

    def _safe_result(self, key: str, locale: str, n: int | None,
                     trace: list[str]) -> ResolveResult:
        return ResolveResult(SAFE_LINE, "safe", key, locale, n, False, trace)
