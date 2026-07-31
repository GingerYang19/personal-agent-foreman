"""_scan_jsonl_processing_time 轮次时长与 _ov_add 总览聚合的边界用例（仅标准库）"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

CAP_SEC = server.SESSION_DUR_CAP * 60


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class ScanJsonlProcessingTimeTest(unittest.TestCase):
    """处理时间 = 每轮 user → 该轮最后一条 assistant 的间隔之和，单轮触顶 SESSION_DUR_CAP"""

    def _write_jsonl(self, records):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for r in records:
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_single_turn_duration(self):
        t0 = 1_700_000_000
        path = self._write_jsonl([
            {"type": "user", "timestamp": iso(t0)},
            {"type": "assistant", "timestamp": iso(t0 + 120)},
        ])
        today_first, total_min, today_min = server._scan_jsonl_processing_time(path, t0 - 3600)
        self.assertEqual(today_first, t0)
        self.assertAlmostEqual(total_min, 2.0)
        self.assertAlmostEqual(today_min, 2.0)

    def test_turn_end_is_last_assistant_and_turns_accumulate(self):
        t0 = 1_700_000_000
        path = self._write_jsonl([
            {"type": "user", "timestamp": iso(t0)},
            {"type": "assistant", "timestamp": iso(t0 + 30)},
            {"type": "assistant", "timestamp": iso(t0 + 60)},   # 同轮取最后一条: 60s
            {"type": "user", "timestamp": iso(t0 + 600)},
            {"type": "assistant", "timestamp": iso(t0 + 780)},  # 第二轮: 180s
        ])
        _, total_min, _ = server._scan_jsonl_processing_time(path, t0 + 10**6)
        self.assertAlmostEqual(total_min, (60 + 180) / 60)

    def test_unanswered_turn_not_counted(self):
        t0 = 1_700_000_000
        path = self._write_jsonl([
            {"type": "user", "timestamp": iso(t0)},
            {"type": "user", "timestamp": iso(t0 + 300)},  # 两轮都没有 assistant 回复
        ])
        _, total_min, today_min = server._scan_jsonl_processing_time(path, t0)
        self.assertEqual(total_min, 0)
        self.assertEqual(today_min, 0)

    def test_single_turn_capped_at_session_dur_cap(self):
        t0 = 1_700_000_000
        path = self._write_jsonl([
            {"type": "user", "timestamp": iso(t0)},
            {"type": "assistant", "timestamp": iso(t0 + CAP_SEC + 3600)},  # 超上限一小时
        ])
        _, total_min, _ = server._scan_jsonl_processing_time(path, t0 + 10**6)
        self.assertAlmostEqual(total_min, server.SESSION_DUR_CAP)

    def test_today_split_only_counts_turns_started_today(self):
        t0 = 1_700_000_000
        today_start = t0 + 500  # 第一轮开始于"昨天"，第二轮开始于"今天"
        path = self._write_jsonl([
            {"type": "user", "timestamp": iso(t0)},
            {"type": "assistant", "timestamp": iso(t0 + 60)},
            {"type": "user", "timestamp": iso(t0 + 600)},
            {"type": "assistant", "timestamp": iso(t0 + 720)},
        ])
        today_first, total_min, today_min = server._scan_jsonl_processing_time(path, today_start)
        self.assertEqual(today_first, t0 + 600)
        self.assertAlmostEqual(total_min, (60 + 120) / 60)
        self.assertAlmostEqual(today_min, 120 / 60)

    def test_missing_file_returns_zeroes(self):
        today_first, total_min, today_min = server._scan_jsonl_processing_time("/nonexistent.jsonl", 0)
        self.assertIsNone(today_first)
        self.assertEqual(total_min, 0)
        self.assertEqual(today_min, 0)


class OvAddTest(unittest.TestCase):
    """_ov_add 依赖真实"今日零点"，用例基于 get_today_range() 构造相对时间戳"""

    def setUp(self):
        self.ov = {}
        self.today_start = server.get_today_range()[0].timestamp()

    def test_no_updated_ts_is_noop(self):
        server._ov_add(self.ov, "A", 1, None)
        self.assertEqual(self.ov, {})

    def test_created_after_updated_clamps_to_zero_duration(self):
        past = self.today_start - 86400
        server._ov_add(self.ov, "A", past + 999, past)  # created > updated
        self.assertEqual(self.ov["A"]["duration_min"], 0)
        self.assertEqual(self.ov["A"]["sessions"], 1)

    def test_session_created_today_counts_full_duration(self):
        created = self.today_start + 600
        updated = created + 1800  # 30min
        server._ov_add(self.ov, "A", created, updated)
        ent = self.ov["A"]
        self.assertEqual(ent["today_count"], 1)
        self.assertAlmostEqual(ent["today_min"], 30.0)
        self.assertAlmostEqual(ent["duration_min"], 30.0)

    def test_duration_capped_and_override_wins(self):
        created = self.today_start - 3 * 86400
        updated = self.today_start - 2 * 86400  # 跨 24h，触顶 SESSION_DUR_CAP
        server._ov_add(self.ov, "A", created, updated)
        self.assertAlmostEqual(self.ov["A"]["duration_min"], server.SESSION_DUR_CAP)
        server._ov_add(self.ov, "B", created, updated, dur_override=12.5)
        self.assertAlmostEqual(self.ov["B"]["duration_min"], 12.5)

    def test_cross_day_session_uses_today_start_override(self):
        created = self.today_start - 86400          # 昨天创建
        updated = self.today_start + 3600           # 今天仍活跃
        first_today = self.today_start + 600
        server._ov_add(self.ov, "A", created, updated, today_start_override=first_today)
        self.assertAlmostEqual(self.ov["A"]["today_min"], (updated - first_today) / 60)

    def test_cross_day_session_without_data_estimates_30min(self):
        created = self.today_start - 86400
        updated = self.today_start + 3600
        server._ov_add(self.ov, "A", created, updated)
        self.assertAlmostEqual(self.ov["A"]["today_min"], 30)

    def test_today_dur_override_has_highest_priority(self):
        created = self.today_start + 60
        updated = created + 6000
        server._ov_add(self.ov, "A", created, updated,
                       today_start_override=created, today_dur_override=7.5)
        self.assertAlmostEqual(self.ov["A"]["today_min"], 7.5)

    def test_accumulates_sessions_and_daily(self):
        created = self.today_start + 60
        updated = created + 600
        server._ov_add(self.ov, "A", created, updated)
        server._ov_add(self.ov, "A", created, updated)
        ent = self.ov["A"]
        self.assertEqual(ent["sessions"], 2)
        self.assertEqual(ent["today_count"], 2)
        day = datetime.fromtimestamp(updated, tz=server.TZ).strftime("%Y-%m-%d")
        self.assertEqual(ent["daily"][day], 2)


if __name__ == "__main__":
    unittest.main()
