"""物理层仿真与两台独立发送驱动。

真值隔离：物理层是唯一读取 ground_truth.json 的运行期模块，它对外只暴露
某次发送的实测收信强度（含在教具模型内），驱动与调度器只持有多面体范围。

功率档策略（满足“按一组观测允许的真实损耗与实际前一帧强度发送”）：
  - 数据帧首帧：按当前可行域最差损耗选最小档（无记忆可用时）。
  - 后续帧/回执：用前一帧实测 RSSI 反推真实路径损耗，闭环选覆盖该真实损耗
    的最小档（仍受有限档位约束，不会无限增大功率）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from plant import (  # noqa: E402
    TX_LEVELS_DBM,
    Receiver,
    total_path_loss,
)
from framing import Frame, ReassemblyBuffer, crc32_hex  # noqa: E402

F = Fraction
ROOT = os.path.join(os.path.dirname(__file__), "..")


class PhysicalPlant:
    """虚构光路物理层：持有隐藏真值，产出实测 RSSI。"""

    def __init__(self, truth_path: str):
        with open(truth_path) as fh:
            truth = json.load(fh)
        self.x = [
            F(str(truth["true_splice_loss_db"][f"j{i}"])) for i in range(1, 5)
        ]

    def true_path_loss(self, path: str) -> Fraction:
        return total_path_loss(path, self.x)

    def transmit(self, tx_dbm: Fraction, path: str) -> Dict:
        loss = self.true_path_loss(path)
        rssi = tx_dbm - loss
        return {"path": path, "tx_dbm": tx_dbm, "pathloss": loss, "rssi_dbm": rssi}


@dataclass
class Driver:
    station: str
    path: str                       # R1 / R2
    receiver_sens: Fraction
    level_table: Dict[str, Fraction]   # 调度器签发的档位表
    plant: PhysicalPlant
    last_measured_pathloss: Optional[Fraction] = None
    buffer: Optional[ReassemblyBuffer] = None

    def level_for_data(self, stream: str, worst_case_ub: Fraction) -> Fraction:
        """数据帧：首帧用最差档；有实测记忆后按真实损耗降档。"""
        if self.last_measured_pathloss is None:
            return self.level_table[stream]
        need = self.receiver_sens + self.last_measured_pathloss
        for lvl in TX_LEVELS_DBM:
            if lvl >= need:
                return lvl
        return TX_LEVELS_DBM[-1]

    def level_for_ack(self, incoming_rssi: Fraction, incoming_tx: Fraction) -> Fraction:
        """回执功率：用前一帧实测强度反推真实损耗，闭环选最小档。"""
        measured_loss = incoming_tx - incoming_rssi
        self.last_measured_pathloss = measured_loss
        need = self.receiver_sens + measured_loss
        for lvl in TX_LEVELS_DBM:
            if lvl >= need:
                return lvl
        return TX_LEVELS_DBM[-1]

    def send(self, frame: Frame, level: Fraction) -> Dict:
        frame.tx_level_dbm = float(level)
        obs = self.plant.transmit(level, self.path)
        frame.crc = crc32_hex(frame.payload)
        return obs


def run_schedule(
    sch,
    drivers: Dict[str, Driver],
    frames_by_stream: Dict[str, List[Frame]],
    ack_frames: Dict[str, List[Frame]],
    receivers: Dict[str, Receiver],
    orders: dict,
) -> dict:
    """按调度表驱动发送，帧进入对应接收器缓冲区；记录 RSSI、CRC、逾窗。"""
    # 每份收件单一个独立重组缓冲区（两份目录页 + 一份访客样件）
    buffers: Dict[str, ReassemblyBuffer] = {
        "catalog_page_1": ReassemblyBuffer("catalog_page_1"),
        "catalog_page_2": ReassemblyBuffer("catalog_page_2"),
        "visitor_sample": ReassemblyBuffer("visitor_sample"),
    }
    # (物理流, seq) → 逻辑件
    channel_of_seq = {
        ("catalog", 0): "catalog_page_1",
        ("catalog", 1): "catalog_page_2",
        ("visitor", 0): "visitor_sample",
    }
    log = []
    delivered = {"catalog": [], "visitor": []}
    acks = {"catalog": [], "visitor": []}
    over_window = []

    stream_cursor = {"catalog": 0, "visitor": 0}
    ack_cursor = {"catalog": 0, "visitor": 0}
    # 站点 → 其接收器（回执发往发起数据的那一站的接收器）
    station_receiver = {"ARCHIVE": "R1", "VISITOR": "R2"}
    deadlines = {o["stream"]: o["deadline_slot"] for o in orders["orders"]}
    ack_deadlines = {o["stream"]: o["ack_deadline_slot"] for o in orders["orders"]}

    for op in sorted(sch.ops, key=lambda o: o.slot):
        if op.op not in ("TX", "RX"):
            entry = {"slot": op.slot, "op": op.op, "station": op.station, "note": op.note}
            # ACK 窗被恢复守卫顶掉：该回执无法在截止前完成，登记逾窗留空
            if op.op == "GUARD" and op.note and "ACK 窗必须留空" in op.note:
                over_window.append({
                    "slot": op.slot, "stream": "visitor", "kind": "ack",
                    "deadline": ack_deadlines["visitor"],
                    "reason": "接收恢复时间未满足，ACK 窗强制留空",
                })
                entry["forced_blank_window"] = True
            log.append(entry)
            continue

        if op.op == "TX":
            stream = op.stream
            if stream not in frames_by_stream:
                continue
            idx = stream_cursor[stream]
            if idx >= len(frames_by_stream[stream]):
                continue
            frame = frames_by_stream[stream][idx]
            driver = drivers[op.station]
            obs = driver.send(frame, op.level_dbm)
            frame.tx_slot = op.slot
            stream_cursor[stream] += 1
            sens = receivers[frame.dst].sensitivity_dbm
            decodable = obs["rssi_dbm"] >= sens
            entry = {
                "slot": op.slot, "op": "TX", "station": op.station, "stream": stream,
                "seq": frame.seq, "tx_dbm": float(obs["tx_dbm"]),
                "pathloss_db": float(obs["pathloss"]),
                "rssi_dbm": float(obs["rssi_dbm"]),
                "sensitivity_dbm": float(sens),
                "decodable": bool(decodable),
                "crc": frame.crc,
            }
            if op.slot >= deadlines[stream]:
                over_window.append({"slot": op.slot, "stream": stream,
                                    "kind": "data", "deadline": deadlines[stream]})
                entry["over_window"] = True
            if decodable:
                channel = channel_of_seq[(stream, frame.seq)]
                # 归一化为逻辑件内部局部分片号（每份件独立从 0 起编）
                local = Frame(frame.stream, 0, True, frame.payload,
                              frame.src, frame.dst, frame.crc)
                buffers[channel].push(local)
                delivered[stream].append(frame.seq)
            log.append(entry)

        elif op.op == "RX":
            # 接收时隙：对端发来的回执帧进入本站缓冲区
            stream = op.stream.replace("_ack", "")
            idx = ack_cursor[stream]
            if idx >= len(ack_frames.get(stream, [])):
                continue
            ack = ack_frames[stream][idx]
            driver = drivers[op.station]
            # 回执功率由前一数据帧实测强度闭环决定
            last_data = next(
                (e for e in reversed(log)
                 if e.get("stream") == stream and e.get("op") == "TX"),
                None,
            )
            if last_data is None:
                continue
            lvl = driver.level_for_ack(
                F(str(last_data["rssi_dbm"])), F(str(last_data["tx_dbm"]))
            )
            obs = driver.send(ack, lvl)
            ack.tx_slot = op.slot
            ack_cursor[stream] += 1
            target_rx = station_receiver[ack.dst]
            sens = receivers[target_rx].sensitivity_dbm
            decodable = obs["rssi_dbm"] >= sens
            entry = {
                "slot": op.slot, "op": "RX(ack)", "station": op.station,
                "stream": stream, "seq": ack.seq, "tx_dbm": float(obs["tx_dbm"]),
                "rssi_dbm": float(obs["rssi_dbm"]),
                "decodable": bool(decodable), "crc": ack.crc,
                "closed_loop_level": True,
            }
            if op.slot >= ack_deadlines[stream]:
                over_window.append({"slot": op.slot, "stream": stream,
                                    "kind": "ack", "deadline": ack_deadlines[stream]})
                entry["over_window"] = True
            if decodable:
                acks[stream].append(ack.seq)
            log.append(entry)

    reassembled = {}
    for channel, buf in buffers.items():
        blob = buf.reassemble()
        reassembled[channel] = {
            "complete": blob is not None,
            "crc_failures": buf.crc_failures,
            "sha256": None if blob is None else __import__("framing").sha256_hex(blob),
            "length": 0 if blob is None else len(blob),
        }

    return {
        "log": log,
        "delivered_seqs": delivered,
        "ack_seqs": acks,
        "reassembly": reassembled,
        "over_window": over_window,
        "reclaimed": sch.reclaimed,
        "rejected": sch.rejected,
        "visitor_capacity_slots": sch.visitor_capacity_slots,
        "level_table": {k: float(v) for k, v in sch.level_table.items()},
    }
