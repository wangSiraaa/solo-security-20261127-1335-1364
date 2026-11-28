"""分帧、CRC32 校验与接收缓冲区重组（纯标准库）。

一帧 = 帧头（源/目的/序号/末帧标志/载荷长度）+ 载荷 + CRC32。
接收端按序号把分片写入每流缓冲区，末帧到齐且全部 CRC 通过后重组，
再与 SHA256 清单核对，产出目录页或访客样件。
"""
from __future__ import annotations

import hashlib
import json
import os
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

FRAME_MAGIC = "FIBERTEACH-1"


def crc32_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Frame:
    stream: str            # catalog（档案室目录）或 visitor（访客样件）
    seq: int
    is_last: bool
    payload: bytes
    src: str
    dst: str
    crc: Optional[str] = None
    # 发送时由物理层回填
    tx_level_dbm: Optional[float] = None
    tx_slot: Optional[int] = None

    def encode(self) -> bytes:
        header = json.dumps(
            {
                "magic": FRAME_MAGIC,
                "stream": self.stream,
                "seq": self.seq,
                "last": self.is_last,
                "src": self.src,
                "dst": self.dst,
                "len": len(self.payload),
                "crc": crc32_hex(self.payload),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return header + b"\n" + self.payload

    @staticmethod
    def decode(wire: bytes) -> "Frame":
        head, _, payload = wire.partition(b"\n")
        meta = json.loads(head.decode("utf-8"))
        assert meta["magic"] == FRAME_MAGIC
        assert crc32_hex(payload) == meta["crc"], "CRC32 校验失败"
        return Frame(
            stream=meta["stream"],
            seq=meta["seq"],
            is_last=meta["last"],
            payload=payload,
            src=meta["src"],
            dst=meta["dst"],
            crc=meta["crc"],
        )


def fragment(stream: str, data: bytes, chunk: int, src: str, dst: str) -> List[Frame]:
    frames = []
    total = (len(data) + chunk - 1) // chunk
    for i in range(total):
        piece = data[i * chunk:(i + 1) * chunk]
        frames.append(
            Frame(
                stream=stream,
                seq=i,
                is_last=(i == total - 1),
                payload=piece,
                src=src,
                dst=dst,
            )
        )
    return frames


@dataclass
class ReassemblyBuffer:
    stream: str
    chunks: Dict[int, bytes] = field(default_factory=dict)
    last_seq: Optional[int] = None
    crc_failures: int = 0

    def push(self, frame: Frame) -> None:
        # 调用方已按 (物理流, seq) 把帧路由到正确的逻辑件缓冲区，这里只做 CRC 核验
        if frame.crc is not None and crc32_hex(frame.payload) != frame.crc:
            self.crc_failures += 1
            return
        self.chunks[frame.seq] = frame.payload
        if frame.is_last:
            self.last_seq = frame.seq

    def is_complete(self) -> bool:
        if self.last_seq is None:
            return False
        return all(i in self.chunks for i in range(self.last_seq + 1))

    def reassemble(self) -> Optional[bytes]:
        if not self.is_complete():
            return None
        return b"".join(self.chunks[i] for i in range(self.last_seq + 1))


def verify_manifest(blob: bytes, manifest_sha256: str) -> bool:
    return sha256_hex(blob) == manifest_sha256


def build_payloads(out_dir: str) -> dict:
    """生成两份带校验的目录页与访客样件（确定性、可复跑）。"""
    os.makedirs(out_dir, exist_ok=True)
    catalog_page1 = (
        "档案室目录页 #1（虚构）\n"
        "架位 A-01..A-12：城建卷宗 2019-0001 至 2019-0012\n"
        "密级：内部；保管期限：30 年；核对人：值班员甲\n"
    ).encode("utf-8")
    catalog_page2 = (
        "档案室目录页 #2（虚构）\n"
        "架位 B-07..B-18：测绘底图 2021-0033 至 2021-0044\n"
        "密级：内部；保管期限：长期；核对人：值班员乙\n"
    ).encode("utf-8")
    visitor_sample = (
        "访客租用时段样件（虚构）\n"
        "采样序列 S-100..S-160，波长 1550nm，封装格式 FT-v3\n"
        "本样件仅用于离线教具的缓冲区重组与逾窗核实。\n"
    ).encode("utf-8")

    payloads = {
        "catalog_page_1": catalog_page1,
        "catalog_page_2": catalog_page2,
        "visitor_sample": visitor_sample,
    }
    manifest = {name: sha256_hex(blob) for name, blob in payloads.items()}
    with open(os.path.join(out_dir, "payload_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    for name, blob in payloads.items():
        with open(os.path.join(out_dir, f"{name}.bin"), "wb") as fh:
            fh.write(blob)
    return manifest
