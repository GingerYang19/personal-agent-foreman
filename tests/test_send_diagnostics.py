"""发话链路诊断关联 id(sid)与 send_log 降级行为的用例（仅标准库,全程 mock 无真实桌面副作用）"""
import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

SID_RE = re.compile(r"\[sid ([0-9a-f]{8})\]")


class InjectFailureCorrelationTest(unittest.TestCase):
    """注入失败时,同一 sid 应出现在 HTTP detail、send.log 与结果文件三处"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.log = os.path.join(d.name, "send.log")
        self.task_file = os.path.join(d.name, "send_task.txt")
        self.result_file = os.path.join(d.name, "send_result.txt")
        for name, val in (("SEND_LOG", self.log), ("SEND_TASK_FILE", self.task_file),
                          ("SEND_RESULT_FILE", self.result_file)):
            p = mock.patch.object(server, name, val)
            p.start()
            self.addCleanup(p.stop)
        # 隔离所有真实桌面副作用: 剪贴板/深链/按键注入/等待
        for target, kw in (("copy_to_clipboard", {}), ("open_url_or_app", {"return_value": ""})):
            p = mock.patch.object(server, target, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("time.sleep")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(server, "find_task", return_value={"id": "task-123", "cwd": ""})
        p.start()
        self.addCleanup(p.stop)
        # SendHelper 调用失败场景: 结果文件不产生,stderr 带错误
        p = mock.patch.object(server.subprocess, "run",
                              return_value=mock.Mock(stderr="helper boom", returncode=1))
        p.start()
        self.addCleanup(p.stop)

    def test_same_sid_in_detail_log_and_result_file(self):
        resp = server.do_send("Qoder", "task-123", "hello")
        self.assertFalse(resp["ok"])
        m = SID_RE.search(resp["detail"])
        self.assertIsNotNone(m, f"detail 缺少 sid: {resp['detail']}")
        sid = m.group(1)
        # 仅凭 send.log 单一文件可用 sid 还原失败原因
        with open(self.log, encoding="utf-8") as f:
            log_text = f.read()
        self.assertIn(f"inject Qoder FAIL sid={sid}", log_text)
        self.assertIn("helper boom", log_text)
        # 结果文件补记了同一 sid
        with open(self.result_file, encoding="utf-8") as f:
            self.assertIn(f"sid={sid}", f.read())

    def test_inject_success_logs_sid(self):
        with open(self.result_file, "w", encoding="utf-8") as f:
            f.write("ok")
        # 注入前 result 文件会被删除,让 mock 的 subprocess.run 重建"ok"结果
        def fake_run(*a, **k):
            with open(self.result_file, "w", encoding="utf-8") as f:
                f.write("ok")
            return mock.Mock(stderr="", returncode=0)

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            resp = server.do_send("Qoder", "task-123", "hello")
        self.assertTrue(resp["ok"])
        sid = SID_RE.search(resp["detail"]).group(1)
        with open(self.log, encoding="utf-8") as f:
            self.assertIn(f"sid={sid}", f.read())

    def test_task_not_found_detail_carries_sid(self):
        with mock.patch.object(server, "find_task", return_value=None):
            resp = server.do_send("Qoder", "nope", "")
        self.assertFalse(resp["ok"])
        self.assertIsNotNone(SID_RE.search(resp["detail"]))


class SendLogDegradeTest(unittest.TestCase):

    def test_unwritable_log_prints_stderr_notice(self):
        bad_path = os.path.join(tempfile.gettempdir(), "no-such-dir-%d" % os.getpid(), "send.log")
        buf = io.StringIO()
        with mock.patch.object(server, "SEND_LOG", bad_path), contextlib.redirect_stderr(buf):
            server.send_log("probe message")
        out = buf.getvalue()
        self.assertIn("[SEND_LOG DEGRADED]", out)
        self.assertIn("probe message", out)

    def test_writable_log_no_stderr_noise(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with mock.patch.object(server, "SEND_LOG", os.path.join(d, "send.log")), \
                    contextlib.redirect_stderr(buf):
                server.send_log("quiet")
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
