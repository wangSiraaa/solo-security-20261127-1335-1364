"""分帧与校验单元测试：CRC32 损坏检测、重组完整性、清单哈希。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from framing import (  # noqa: E402
    Frame,
    ReassemblyBuffer,
    crc32_hex,
    sha256_hex,
    fragment,
    verify_manifest,
)


def test_crc_detects_corruption() -> None:
    f = Frame("s", 0, True, b"important-catalog-bytes", "A", "B")
    f.crc = crc32_hex(f.payload)
    # 传输中载荷被篡改，CRC 不再匹配
    corrupt = Frame("s", 0, True, b"important-catalog-byteX", "A", "B", crc=f.crc)
    buf = ReassemblyBuffer("s")
    buf.push(corrupt)
    assert buf.crc_failures == 1, "损坏帧必须计入 CRC 失败"
    assert not buf.is_complete(), "损坏帧不得触发完整重组"

    # 完整未损坏帧可正常重组
    good = Frame("s", 0, True, b"important-catalog-bytes", "A", "B", crc=f.crc)
    buf2 = ReassemblyBuffer("s")
    buf2.push(good)
    assert buf2.is_complete() and buf2.reassemble() == b"important-catalog-bytes"
    print("CRC32 损坏检测与重组测试通过")


def test_multifragment_reassembly_order() -> None:
    data = bytes(range(200))
    frames = fragment("v", data, 37, "V", "R")
    assert len(frames) == 6 and frames[-1].is_last
    buf = ReassemblyBuffer("v")
    # 乱序投递也必须按 seq 正确重组
    for fr in reversed(frames):
        fr.crc = crc32_hex(fr.payload)
        buf.push(fr)
    assert buf.is_complete()
    assert buf.reassemble() == data
    print("多分片乱序重组测试通过")


def test_manifest_hash() -> None:
    blob = b"visitor-sample-blob"
    digest = sha256_hex(blob)
    assert verify_manifest(blob, digest)
    assert not verify_manifest(blob + b"x", digest)
    print("SHA256 清单核验测试通过")


def test_encode_decode_roundtrip() -> None:
    f = Frame("catalog", 2, False, "目录页分片".encode("utf-8"), "ARCHIVE", "R1")
    wire = f.encode()
    back = Frame.decode(wire)
    assert back.payload == f.payload and back.seq == 2 and back.stream == "catalog"
    print("编解码往返测试通过")


if __name__ == "__main__":
    test_crc_detects_corruption()
    test_multifragment_reassembly_order()
    test_manifest_hash()
    test_encode_decode_roundtrip()
    print("全部分帧校验测试通过")
