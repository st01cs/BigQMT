import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from bigqmt.core.qmt import cli


class StubManager:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def status(self):
        return {
            "state": "ready",
            "process_running": True,
            "logged_in": True,
            "trading_ready": True,
            "can_login": True,
            "restart_count": 0,
            "last_error": None,
            "config": {
                "exe_path": r"D:\QMT\bin.x64\XtItClient.exe",
                "userdata_path": r"D:\QMT\userdata_mini",
                "account_id": "12345678",
                "session_id": None,
                "process_names": ["XtItClient.exe"],
                "auto_start": True,
                "auto_restart": True,
                "max_restarts": 3,
                "poll_interval": 20.0,
            },
        }

    def start(self, timeout=None):
        self.calls.append("start")
        return self.ok

    def login(self, timeout=None):
        self.calls.append("login")
        return self.ok

    def stop(self, close_client=False):
        self.calls.append("stop")
        return True

    def restart(self, timeout=None, close_client=True):
        self.calls.append("restart")
        return self.ok


class CliTest(unittest.TestCase):
    def _run(self, argv, manager):
        buf = io.StringIO()
        with mock.patch.object(cli, "get_qmt_manager", return_value=manager):
            with redirect_stdout(buf):
                code = cli.main(argv)
        return code, buf.getvalue()

    def test_status_prints_and_returns_zero(self):
        code, out = self._run(["status"], StubManager())
        self.assertEqual(code, 0)
        self.assertIn("state", out)
        self.assertIn("ready", out)

    def test_start_ok(self):
        mgr = StubManager(ok=True)
        code, _ = self._run(["start"], mgr)
        self.assertEqual(code, 0)
        self.assertIn("start", mgr.calls)

    def test_start_failure_returns_one(self):
        mgr = StubManager(ok=False)
        code, _ = self._run(["start"], mgr)
        self.assertEqual(code, 1)

    def test_login_ok(self):
        mgr = StubManager(ok=True)
        code, _ = self._run(["login"], mgr)
        self.assertEqual(code, 0)
        self.assertIn("login", mgr.calls)

    def test_stop_passes_close_client_flag(self):
        mgr = StubManager()
        with mock.patch.object(mgr, "stop", return_value=True) as stop_mock:
            code, _ = self._run(["stop", "--close-client"], mgr)
        self.assertEqual(code, 0)
        stop_mock.assert_called_once_with(close_client=True)

    def test_restart_ok(self):
        mgr = StubManager(ok=True)
        code, _ = self._run(["restart"], mgr)
        self.assertEqual(code, 0)
        self.assertIn("restart", mgr.calls)


if __name__ == "__main__":
    unittest.main()
