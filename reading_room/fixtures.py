"""Deterministic fixture builder for the reading-room demo and tests.

Running :func:`build` materialises

* a local image supply station tree (public + subscriber namespaces), and
* one or more wire mailboxes (JSON-ready dicts),

that together exercise every stated scenario:

* three image ownerships -- inline, shared public, per-subscriber,
* two letters whose promo pictures are byte-identical but differently owned,
* one letter with an accompanying map, one with only address + text,
* a short stair-only passage that exists only on the shared common map,
* a longer, reliable step-free passage written in both letters,
* an organiser step-free detour that exists only in letter A's map text,
* a deliberately missing public illustration to exercise substitution.

``build`` is pure and deterministic: the same arguments always produce the
same bytes.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

ISSUE = "issue1420"

NODE_LABELS = {
    "gate": "到达门（临街入口）",
    "lobby": "前厅",
    "east_corridor": "东廊",
    "west_corridor": "西廊",
    "venue": "会场（报告厅）",
}

#: Two visually identical promos (same bytes) with different ownership.
PROMO_RGB = (180, 60, 60)


# ---------------------------------------------------------------------------
# Minimal PNG encoder (no third-party deps)
# ---------------------------------------------------------------------------

def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    r, g, b = rgb

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes([r, g, b]) * width
    raw = row * height
    idat = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def images() -> dict[str, bytes]:
    """Return the supply-station files keyed by request path."""
    return {
        f"/public/{ISSUE}/common_map.png": make_png(64, 48, (30, 90, 160)),
        f"/public/{ISSUE}/map_a.png": make_png(64, 48, (40, 140, 70)),
        f"/public/{ISSUE}/map_a_revised.png": make_png(64, 48, (40, 110, 90)),
        f"/public/{ISSUE}/shared_illu.png": make_png(32, 32, (150, 150, 150)),
        f"/public/{ISSUE}/a_only.png": make_png(24, 24, (200, 40, 40)),
        f"/public/{ISSUE}/b_only.png": make_png(24, 24, (230, 140, 20)),
        # Present on the station but NEVER fetched: per-subscriber namespace.
        # Byte-identical to letter A's inline promo, so the two "look the same".
        "/sub/reader_b/promo.png": make_png(16, 16, PROMO_RGB),
        # A third subscriber's own promo address (never fetched either).
        "/sub/reader_c/promo.png": make_png(16, 16, (60, 90, 200)),
        # NOTE: /public/issue1420/lost.png is intentionally absent -> 404.
    }


def promo_inline_bytes() -> bytes:
    """Bytes for letter A's inline promo (identical to reader B's sub image)."""
    return make_png(16, 16, PROMO_RGB)


# ---------------------------------------------------------------------------
# Shared graph fragments
# ---------------------------------------------------------------------------

def letter_text_directions() -> dict:
    """Reliable, longer, fully step-free route, in both letters."""
    return {
        "label": "letter_text",
        "notes": ["随信文字方向：沿东廊步行至会场，路程较长但全程平层。"],
        "edges": [
            {"u": "gate", "v": "lobby", "access": "corridor", "weight": 2,
             "note": "入口至前厅"},
            {"u": "lobby", "v": "east_corridor", "access": "corridor", "weight": 2,
             "note": "前厅转东廊"},
            {"u": "east_corridor", "v": "venue", "access": "corridor", "weight": 2,
             "note": "东廊至会场"},
        ],
    }


def common_map_description() -> dict:
    """Shared issue illustration: the short stair-only passage lives here only."""
    return {
        "label": "common_map",
        "notes": ["公共图：前厅有经楼梯直达会场的近路（仅公共图标出）。"],
        "edges": [
            {"u": "gate", "v": "lobby", "access": "corridor", "weight": 1,
             "note": "入口至前厅短廊"},
            {"u": "lobby", "v": "venue", "access": "stairs", "weight": 1,
             "note": "前厅经楼梯直达会场（仅公共图标注的近路）"},
        ],
    }


def letter_a_map_description() -> dict:
    """Letter A's accompanying map: organiser's step-free detour."""
    return {
        "label": "letter_a:map",
        "notes": [
            "随信地图：轮椅请由入口缓坡经前厅转西廊，走西廊坡道入会场。",
            "此无障碍绕行信息仅见于随信地图及本说明。",
        ],
        "edges": [
            {"u": "gate", "v": "lobby", "access": "ramp", "weight": 1,
             "note": "入口缓坡"},
            {"u": "lobby", "v": "west_corridor", "access": "corridor", "weight": 1,
             "note": "前厅转西廊"},
            {"u": "west_corridor", "v": "venue", "access": "ramp", "weight": 1,
             "note": "西廊坡道入会场（无障碍绕行）"},
        ],
    }


def letter_a_map_description_revised() -> dict:
    """Revised map: the lobby->west corridor is now one-way toward the lobby."""
    return {
        "label": "letter_a:map:revised",
        "notes": [
            "随信地图（修订）：西廊现为单向，只能由西廊返回前厅，不能反向进入。",
            "轮椅请改走东廊平层通道。",
        ],
        "edges": [
            {"u": "gate", "v": "lobby", "access": "ramp", "weight": 1,
             "note": "入口缓坡"},
            {"u": "west_corridor", "v": "lobby", "access": "corridor", "weight": 1,
             "one_way": True, "note": "西廊→前厅单向"},
            {"u": "west_corridor", "v": "venue", "access": "ramp", "weight": 1,
             "note": "西廊坡道入会场"},
        ],
    }


def _asset(asset_id, kind, *, url="", role="decor", alt="", media_type="image/png",
           bytes_b64=""):
    a = {
        "asset_id": asset_id,
        "kind": kind,
        "role": role,
        "alt": alt,
        "media_type": media_type,
    }
    if url:
        a["url"] = url
    if bytes_b64:
        a["bytes_b64"] = bytes_b64
    return a


def build_mailbox(revised_map: bool = False) -> dict:
    """Assemble the wire mailbox dict.

    ``revised_map=True`` replaces *only* letter A's accompanying map asset and
    its description; everything else is byte-for-byte identical.
    """
    promo_b64 = _b64(promo_inline_bytes())

    map_url = (
        f"/public/{ISSUE}/map_a_revised.png" if revised_map
        else f"/public/{ISSUE}/map_a.png"
    )
    map_desc = (
        letter_a_map_description_revised() if revised_map
        else letter_a_map_description()
    )
    map_alt = "随信地图（修订）" if revised_map else "随信地图"

    letter_a = {
        "letter_id": "letter_a",
        "subscriber": "读者甲",
        "title": "讲座通知 · 甲",
        "body_text": (
            "尊敬的读者甲：现邀请您参加本期讲座。请凭下方活动号选择场次，"
            "并于到达门按指引入场。随信附地图一张。"
        ),
        "arrival_gate": "gate",
        "events": [
            {
                "event_number": "A-101",
                "title": "城市步行与无障碍设计",
                "venue": "会场（报告厅）",
                "venue_node": "venue",
                "note": "18:30 入场，19:00 开讲。",
            }
        ],
        "directions": letter_text_directions(),
        "assets": [
            _asset("promo_a", "inline", role="promo", alt="讲座主题图（邮件自带）",
                   bytes_b64=promo_b64),
            _asset("a_shared", "public", url=f"/public/{ISSUE}/shared_illu.png",
                   role="decor", alt="本期公共插图"),
            _asset("a_only", "public", url=f"/public/{ISSUE}/a_only.png",
                   role="decor", alt="甲专属公共插图"),
        ],
        "letter_map": {
            **_asset("map_a", "public", url=map_url, role="map", alt=map_alt),
            "description": map_desc,
        },
    }

    letter_b = {
        "letter_id": "letter_b",
        "subscriber": "读者乙",
        "title": "讲座通知 · 乙",
        "body_text": (
            "尊敬的读者乙：现邀请您参加本期讲座（本信无随信地图，"
            "请按文字方向入场）。请凭活动号选择场次。"
        ),
        "arrival_gate": "gate",
        "events": [
            {
                "event_number": "B-202",
                "title": "港口史话",
                "venue": "会场（报告厅）",
                "venue_node": "venue",
                "note": "20:00 入场，20:30 开讲。",
            }
        ],
        "directions": letter_text_directions(),
        "assets": [
            # Same-looking promo, but bound to this subscriber's address.
            _asset("promo_b", "subscriber",
                   url="/sub/reader_b/promo.png", role="promo",
                   alt="讲座主题图（订阅者专属）"),
            _asset("b_shared", "public", url=f"/public/{ISSUE}/shared_illu.png",
                   role="decor", alt="本期公共插图"),
            _asset("b_only", "public", url=f"/public/{ISSUE}/b_only.png",
                   role="decor", alt="乙专属公共插图"),
            # Public but missing at the station -> substituted at seal time.
            _asset("lost", "public", url=f"/public/{ISSUE}/lost.png",
                   role="decor", alt="一份未能取得的公共插图"),
        ],
    }

    letter_c = {
        "letter_id": "letter_c",
        "subscriber": "读者丙",
        "title": "讲座通知 · 丙",
        "body_text": (
            "尊敬的读者丙：现邀请您参加本期讲座（本信仅地址与文字方向）。"
            "请凭活动号选择场次。"
        ),
        "arrival_gate": "gate",
        "events": [
            {
                "event_number": "C-303",
                "title": "修复装帧入门",
                "venue": "会场（报告厅）",
                "venue_node": "venue",
                "note": "14:00 入场，14:30 开讲。",
            }
        ],
        "directions": letter_text_directions(),
        "assets": [
            # Third subscriber's own (these too must never be requested).
            _asset("promo_c", "subscriber",
                   url="/sub/reader_c/promo.png", role="promo",
                   alt="讲座主题图（丙订阅者专属）"),
            _asset("c_shared", "public", url=f"/public/{ISSUE}/shared_illu.png",
                   role="decor", alt="本期公共插图"),
        ],
    }

    return {
        "issue": ISSUE,
        "node_labels": NODE_LABELS,
        "common_map": {
            **_asset("common_map", "public",
                    url=f"/public/{ISSUE}/common_map.png", role="map",
                    alt="本期公共场馆图"),
            "description": common_map_description(),
        },
        "letters": [letter_a, letter_b, letter_c],
    }


def write_station(root) -> None:
    """Write the supply-station image tree under ``root``."""
    root = Path(root)
    for url_path, data in images().items():
        disk = root / url_path.lstrip("/")
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(data)


def write_fixtures(fixtures_dir) -> dict[str, Path]:
    """Write mailbox JSON files (base + revised-map variant)."""
    import json

    fixtures_dir = Path(fixtures_dir)
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    base = build_mailbox(revised_map=False)
    p = fixtures_dir / "issue.json"
    p.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["issue"] = p
    revised = build_mailbox(revised_map=True)
    p2 = fixtures_dir / "issue_revised_map.json"
    p2.write_text(json.dumps(revised, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["issue_revised_map"] = p2
    return paths


def ensure_fixtures(root) -> tuple[Path, Path]:
    """Write fixtures + station files under ``root`` if missing. Returns paths."""
    root = Path(root)
    fixtures_dir = root / "fixtures"
    station_dir = root / "supply_station" / "assets"
    if not (fixtures_dir / "issue.json").exists() or not (
        fixtures_dir / "issue_revised_map.json"
    ).exists():
        write_fixtures(fixtures_dir)
    if not station_dir.exists() or not any(station_dir.rglob("*.png")):
        write_station(station_dir)
    return fixtures_dir, station_dir


if __name__ == "__main__":  # pragma: no cover - manual helper
    here = Path(__file__).resolve().parent.parent
    write_station(here / "supply_station" / "assets")
    write_fixtures(here / "fixtures")
    print("fixtures and station assets written")
