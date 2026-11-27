"""fwstack 离线工具的单元测试：python3 -m unittest discover -s tests。"""
import json
import os
import tempfile
import unittest

from fwstack.analyzer import analyze
from fwstack.deliver import build_config, load_config, save_config, verify_fingerprints
from fwstack.interpreter import ReplayError, replay
from fwstack.model import ISOLATED, SHARED
from fwstack.parser import ParseError, parse_board_file, parse_program_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "examples/programs/sampler.fw")
PROG_LATE = os.path.join(ROOT, "examples/programs/sampler_late.fw")
PROG_WIDE = os.path.join(ROOT, "examples/programs/sampler_wide.fw")
BOARDS = os.path.join(ROOT, "examples/boards/boards.fwboard")


def board(name):
    return parse_board_file(BOARDS, name)


class ParserTests(unittest.TestCase):
    def test_parses_program_and_board(self):
        prog = parse_program_file(PROG)
        self.assertEqual(set(prog.modes), {"SAMPLE", "CALIB"})
        self.assertEqual(set(prog.isrs), {"high", "low"})
        nano = board("NANO")
        self.assertEqual(nano.ram_total, 1024)
        self.assertEqual([(s, z) for s, z in nano.free_gaps()], [(0, 896)])

    def test_reserved_fragments(self):
        nano_p = board("NANO_P")
        self.assertEqual(nano_p.free_gaps(), [(0, 704), (784, 112)])

    def test_rejects_call_to_handler(self):
        bad = """
        program p {
          mode A entry e;
          irq high handler h arrivals 1 per_period;
          irq low handler l arrivals 1 per_period;
          func e(frame 10 bytes, work 1 us) { call h; }
          func h(frame 10 bytes, work 1 us) {}
          func l(frame 10 bytes, work 1 us) {}
        }"""
        with self.assertRaises(ParseError):
            from fwstack.parser import parse_program_text
            parse_program_text(bad)

    def test_rejects_overlapping_reserves(self):
        bad = """
        board B {
          ram 100 bytes;
          reserve a [10, 30);
          reserve b [20, 40);
          period 100 us;
          deadline high 10 us;
          deadline low 20 us;
        }"""
        from fwstack.parser import parse_boards_text
        with self.assertRaises(ParseError):
            parse_boards_text(bad)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.prog = parse_program_file(PROG)
        self.nano = board("NANO")

    def test_baseline_numbers(self):
        r = analyze(self.prog, self.nano)
        self.assertTrue(r.ok, r.gaps)
        self.assertEqual(r.mode_reports["SAMPLE"].fg_peak, 344)
        self.assertEqual(r.level_reports["high"].worst_response, 1150)
        self.assertEqual(r.level_reports["low"].worst_response, 1950)
        # 高打断低的嵌套中断栈峰值
        self.assertEqual(r.level_reports["low"].nested_peak, 420)
        # 共享栈：最深前台帧 + 嵌套中断链
        self.assertEqual(r.plans[SHARED].peak, 704)
        # 隔离栈：前台峰值 + 中断栈峰值
        self.assertEqual(r.plans[ISOLATED].peak, 352 + 420)
        self.assertTrue(r.plans[SHARED].fits)
        self.assertTrue(r.plans[ISOLATED].fits)

    def test_late_variant_breaks_high_deadline(self):
        r = analyze(parse_program_file(PROG_LATE), self.nano)
        self.assertFalse(r.ok)
        self.assertTrue(any("响应违约" in g and "high" in g for g in r.gaps))
        self.assertEqual(r.level_reports["high"].worst_response, 1640)

    def test_wide_variant_has_shortest_closed_window(self):
        r_base = analyze(self.prog, self.nano)
        r_wide = analyze(parse_program_file(PROG_WIDE), self.nano)
        self.assertLess(r_wide.level_reports["high"].max_closed,
                        r_base.level_reports["high"].max_closed)
        self.assertEqual(r_wide.level_reports["high"].worst_response, 560)

    def test_fragmentation_blocks_isolated_but_not_shared(self):
        r = analyze(self.prog, board("NANO_P"))
        self.assertTrue(r.plans[SHARED].fits)
        self.assertFalse(r.plans[ISOLATED].fits)
        self.assertTrue(any("碎片" in g for g in r.plans[ISOLATED].gaps))

    def test_pure_fragmentation_despite_sufficient_total(self):
        # NANO_F：空闲总量 800B > 隔离栈 772B，但每块仅 400B，两种接法都失败
        r = analyze(self.prog, board("NANO_F"))
        self.assertEqual(sum(s for _, s in board("NANO_F").free_gaps()), 800)
        self.assertFalse(r.plans[SHARED].fits)
        self.assertFalse(r.plans[ISOLATED].fits)
        for plan in r.plans.values():
            self.assertTrue(all("碎片" in g for g in plan.gaps))

    def test_tight_deadline_board(self):
        r = analyze(parse_program_file(PROG_WIDE), board("MID"))
        self.assertTrue(r.ok, r.gaps)
        r2 = analyze(self.prog, board("MID"))
        self.assertFalse(r2.ok)  # 基线版 W=950，high 响应 1150 > 600


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prog = parse_program_file(PROG)
        self.board = board("NANO")
        self.result = analyze(self.prog, self.board)

    def test_build_and_replay_shared(self):
        cfg = build_config(self.result, SHARED)
        path = os.path.join(self.tmp, "cfg.json")
        save_config(cfg, path)
        loaded = load_config(path)
        self.assertEqual(verify_fingerprints(loaded, ROOT), [])
        # low 在 sensor_read 调用树内进入，high 再在 low 的 lo_window 内
        # 嵌套（high 启动早于 low 完成即证明嵌套）。实测峰值不得超过静态
        # 分析给出的 704B 安全上界。
        events = [
            {"at": 1000, "level": "low"},
            {"at": 1500, "level": "high"},
            {"at": 1900, "level": "high"},
        ]
        rep = replay(loaded, events, mode="SAMPLE", base_dir=ROOT)
        self.assertTrue(rep.ok, rep.violations)
        self.assertEqual(rep.stack_model, SHARED)
        self.assertLessEqual(rep.peak_bytes, loaded["guarantees"]["shared_peak_bytes"])
        self.assertEqual(loaded["guarantees"]["shared_peak_bytes"], 704)
        self.assertGreater(rep.peak_bytes,
                           loaded["guarantees"]["foreground_peak_bytes"])
        served = [r for r in rep.records if r.state == "served"]
        self.assertEqual(len(served), 3)
        # 高优先级在低级处理期间启动并先于低级完成 -> 嵌套返回
        hi = next(r for r in rep.records if r.level == "high" and r.arrival == 1500)
        lo = rep.records[0]
        self.assertGreater(hi.started, lo.started)
        self.assertLess(hi.finished, lo.finished)
    def test_isolated_capacity_accounting(self):
        cfg = build_config(self.result, ISOLATED)
        events = [{"at": 1000, "level": "low"}, {"at": 1100, "level": "high"}]
        rep = replay(cfg, events, mode="SAMPLE", base_dir=ROOT)
        self.assertTrue(rep.ok, rep.violations)
        # 隔离栈：任一侧物理深度都不超过中断栈 420B
        self.assertLessEqual(rep.peak_bytes, 420)

    def test_replay_rejects_over_bound_event_list(self):
        cfg = build_config(self.result, SHARED)
        events = [{"at": i * 100, "level": "high"} for i in range(3)]
        with self.assertRaises(ReplayError):
            replay(cfg, events, base_dir=ROOT)

    def test_fingerprint_fails_after_program_change(self):
        cfg = build_config(self.result, SHARED)
        # 把交付配置指向“仅移动开放点”的变体，指纹必须失配
        cfg["program"]["path"] = os.path.relpath(PROG_LATE, ROOT)
        problems = verify_fingerprints(cfg, ROOT)
        self.assertEqual(len(problems), 1)
        self.assertIn("指纹失配", problems[0])
        with self.assertRaises(ReplayError):
            replay(cfg, [{"at": 100, "level": "high"}], base_dir=ROOT)

    def test_cannot_deliver_unfit_model(self):
        r = analyze(self.prog, board("NANO_P"))
        with self.assertRaises(ValueError):
            build_config(r, ISOLATED)


class ReplaySemanticsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = build_config(
            analyze(parse_program_file(PROG), board("NANO")), SHARED
        )

    def test_deferred_event_waits_for_open_region(self):
        # 事件在封闭区间（setup 阶段）到达，必须推迟到 sample_window 才启动
        events = [{"at": 100, "level": "high"}]
        rep = replay(self.cfg, events, mode="SAMPLE", base_dir=ROOT)
        self.assertTrue(rep.ok, rep.violations)
        rec = rep.records[0]
        # 封闭区间 950us 后才进入开放区间
        self.assertGreaterEqual(rec.started, 950)
        self.assertEqual(rec.latency, rec.started - rec.arrival)

    def test_deadline_violation_reported(self):
        # 构造一块能装下、但 high 最晚响应卡到 500us 的临时板卡：
        # 静态分析仍达标（最坏响应 560 不达标，故改用 600 交付），
        # 再把配置中的 deadline 收紧到 500，回放必须报告违约。
        wide = parse_program_file(PROG_WIDE)
        tight = board("MID")                      # high deadline 600，最坏 560 达标
        cfg = build_config(analyze(wide, tight), SHARED)
        cfg["guarantees"]["deadline_us"]["high"] = 500
        events = [{"at": 100, "level": "high"}]
        rep = replay(cfg, events, mode="SAMPLE", base_dir=ROOT)
        self.assertFalse(rep.ok)
        self.assertTrue(any("超过最晚响应" in v for v in rep.violations))


if __name__ == "__main__":
    unittest.main()
