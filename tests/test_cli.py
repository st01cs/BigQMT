import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from bigqmt.config import QmtConfig
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


class StubRunner:
    def __init__(self, spec):
        self.spec = spec

    def wait(self, timeout=None):
        return "stopped"

    def stop_external(self):
        return True


class StubSupervisor:
    def __init__(self, manager, runner):
        self.manager = manager
        self.runner = runner
        self.starts = 0

    def start(self, timeout=None):
        self.starts += 1
        return True

    def stop(self, close_client=False):
        return True

    def status(self):
        return {
            "state": "ready",
            "process_running": True,
            "logged_in": True,
            "trading_ready": True,
            "can_login": True,
            "restart_count": 0,
            "last_error": None,
            "config": {"exe_path": None, "userdata_path": None, "account_id": None},
            "strategy": {
                "state": "running",
                "running": True,
                "pid": 1234,
                "mode": "supervise",
                "restart_count": 0,
                "last_error": None,
                "log_file": "logs/strategy.log",
            },
        }


def _patch_cli_strategy():
    """把 CLI 的真实依赖替换为桩，便于测试策略相关命令。"""
    return (
        mock.patch.object(cli, "StrategyRunner", StubRunner),
        mock.patch.object(cli, "StrategySupervisor", StubSupervisor),
    )


class StrategyCliTest(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        patches = [mock.patch.object(cli, "get_qmt_manager", return_value=StubManager())]
        patches.extend(_patch_cli_strategy())
        with mock.patch.object(cli, "load_qmt_config", return_value=QmtConfig()):
            with mock.patch.object(cli, "_run_foreground", return_value=0):
                with patches[0], patches[1], patches[2]:
                    with redirect_stdout(buf):
                        code = cli.main(argv)
        return code, buf.getvalue()

    def test_start_with_strategy_overrides_config_and_foregrounds(self):
        code, out = self._run(["start", "--strategy", "python x.py"])
        self.assertEqual(code, 0)
        self.assertIn("策略已启动", out)

    def test_cmd_start_with_strategy_missing_cmd_returns_one(self):
        with mock.patch.object(cli, "load_qmt_config", return_value=QmtConfig()):
            with mock.patch.object(cli, "get_qmt_manager", return_value=StubManager()):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    args = mock.Mock(strategy=None, strategy_mode=None, timeout=None)
                    code = cli._cmd_start_with_strategy(args)
        self.assertEqual(code, 1)

    def test_strategy_status_prints_and_returns_zero(self):
        code, out = self._run(["strategy", "status", "--strategy", "python x.py"])
        self.assertEqual(code, 0)
        self.assertIn("strategy state", out)

    def test_strategy_stop_returns_zero(self):
        code, _ = self._run(["strategy", "stop", "--strategy", "python x.py"])
        self.assertEqual(code, 0)

    def test_strategy_start_dispatches_to_foreground(self):
        code, out = self._run(["strategy", "start", "--strategy", "python x.py"])
        self.assertEqual(code, 0)
        self.assertIn("策略已启动", out)

    def test_start_with_strategy_bad_mode_rejected_by_parser(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(
                ["start", "--strategy", "python x.py", "--strategy-mode", "bad"]
            )


class StrategyConfigCliTest(unittest.TestCase):
    """_config_with_strategy 命令叠加行为。"""

    def test_overrides_cmd_and_mode(self):
        with mock.patch.object(
            cli, "load_qmt_config", return_value=QmtConfig(strategy_mode="once")
        ):
            args = mock.Mock(
                strategy="python y.py", strategy_mode="supervise", strategy_action="start"
            )
            config = cli._config_with_strategy(args)
        self.assertTrue(config.strategy_enabled)
        self.assertEqual(config.strategy_cmd, "python y.py")
        self.assertEqual(config.strategy_mode, "supervise")

    def test_no_override_when_not_given(self):
        with mock.patch.object(
            cli,
            "load_qmt_config",
            return_value=QmtConfig(strategy_enabled=True, strategy_cmd="python z.py"),
        ):
            args = mock.Mock(strategy=None, strategy_mode=None, strategy_action="start")
            config = cli._config_with_strategy(args)
        self.assertEqual(config.strategy_cmd, "python z.py")


if __name__ == "__main__":
    unittest.main()
