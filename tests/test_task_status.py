"""task_status 三态判定与 agent_result 状态聚合的边界用例（仅标准库）"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

NOW = 1_700_000_000.0  # 固定"当前时间"，用例不依赖真实时钟


class TaskStatusTest(unittest.TestCase):
    """三态语义: <60s 有写入=working; <15min 且最后是 assistant=waiting; 其余=idle"""

    def setUp(self):
        patcher = mock.patch("time.time", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_timestamp_is_idle(self):
        self.assertEqual(server.task_status(None, "assistant"), "idle")

    def test_recent_write_is_working_regardless_of_role(self):
        self.assertEqual(server.task_status(NOW, "user"), "working")
        self.assertEqual(server.task_status(NOW - 59.9, None), "working")

    def test_working_boundary_at_60s(self):
        # 恰好 60s 不再算 working；assistant 落入 waiting，user 落入 idle
        self.assertEqual(server.task_status(NOW - 60, "assistant"), "waiting")
        self.assertEqual(server.task_status(NOW - 60, "user"), "idle")

    def test_waiting_requires_assistant_last(self):
        self.assertEqual(server.task_status(NOW - 300, "assistant"), "waiting")
        self.assertEqual(server.task_status(NOW - 300, "user"), "idle")
        self.assertEqual(server.task_status(NOW - 300, None), "idle")

    def test_waiting_window_boundary_at_900s(self):
        self.assertEqual(server.task_status(NOW - 899.9, "assistant"), "waiting")
        self.assertEqual(server.task_status(NOW - 900, "assistant"), "idle")


class AgentResultTest(unittest.TestCase):

    @staticmethod
    def _task(tid, status, ts):
        return {"id": tid, "status": status, "last_active_ts": ts}

    def test_status_priority_and_counts(self):
        tasks = [self._task("a", "working", 3),
                 self._task("b", "waiting", 2),
                 self._task("c", "idle", 1)]
        r = server.agent_result("X", tasks, "ui")
        self.assertEqual(r["status"], "working")
        self.assertEqual(r["working_count"], 1)
        self.assertEqual(r["waiting_count"], 1)
        self.assertEqual(r["today_count"], 3)

    def test_waiting_when_no_working(self):
        r = server.agent_result("X", [self._task("a", "waiting", 1),
                                      self._task("b", "idle", 2)], "ui")
        self.assertEqual(r["status"], "waiting")

    def test_idle_offline_and_error(self):
        self.assertEqual(server.agent_result("X", [self._task("a", "idle", 1)], "ui")["status"], "idle")
        self.assertEqual(server.agent_result("X", [], "ui")["status"], "offline")
        r = server.agent_result("X", [self._task("a", "working", 1)], "ui", error="boom")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["error"], "boom")

    def test_recent_tasks_sorted_desc_and_capped_to_6(self):
        tasks = [self._task(str(i), "idle", i) for i in range(8)]
        r = server.agent_result("X", tasks, "ui")
        self.assertEqual(len(r["recent_tasks"]), 6)
        self.assertEqual([t["id"] for t in r["recent_tasks"]],
                         ["7", "6", "5", "4", "3", "2"])
        self.assertEqual(r["today_count"], 8)


if __name__ == "__main__":
    unittest.main()
