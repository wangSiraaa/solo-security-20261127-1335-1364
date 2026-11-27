"""页摘要与页级核验。

封卷时另存“页号 + 摘要清单”（manifest）；
恢复台从实际卷副本取回页面后，逐页对清单核对，
清单对不上（旧页、串卷、缺页）的页面不能作为核准原页交回。
"""

import hashlib
import hmac


def page_digest(volume_id: str, page_no: int, content: bytes) -> str:
    """内容摘要：sha256(卷号 || 页号 || 内容)，防止跨卷/跨页顶替。"""
    h = hashlib.sha256()
    h.update(volume_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(page_no.to_bytes(4, "big"))
    h.update(content)
    return h.hexdigest()


def page_tag(key: bytes, volume_id: str, page_no: int, content: bytes) -> bytes:
    """页级 HMAC：开封材料拼齐后，用还原出的卷钥验证副本确为本卷本代页面。"""
    msg = volume_id.encode("utf-8") + b"\x00" + page_no.to_bytes(4, "big") + content
    return hmac.new(key, msg, hashlib.sha256).digest()


def verify_page_tag(
    key: bytes, volume_id: str, page_no: int, content: bytes, tag: bytes
) -> bool:
    return hmac.compare_digest(page_tag(key, volume_id, page_no, content), tag)
