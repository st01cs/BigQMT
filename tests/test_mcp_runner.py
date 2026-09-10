"""MCP 服务运行器测试（进程管理复用 StrategyRunner）。"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bigqmt.mcp import runner as runner_mod
from bigqmt.mcp.client import QMTApiError
from bigqmt.core.qmt._strategy import build_command
from bigqmt.mcp.config import McpConfig
from bigqmt.mcp.runner import (
    REPO_ROOT,
    build_mcp_command,
    build_mcp_spec,
    build_runner,
    default_log_file,
    default_pid_file,
)
from tests.test_strategy_runner import FakeProcess, FakeSpawner


class BuildCommandTest(unittest.TestCase):
    def test_default_command(self):
        command = build_mcp_command(McpConfig())
        self.assertIn("python -m bigqmt.mcp", command)
        self.assertIn("--host 127.0.0.1", command)
        self.assertIn("--port 9000", command)
        self.assertIn("--qmt-url http://127.0.0.1:10086", command)
        self.assertNotIn("--allow-remote", command)

    def test_allow_remote_flag_added(self):
        command = build_mcp_command(McpConfig(host="0.0.0.0", allow_remote=True, port=9100))
        self.assertIn("--host 0.0.0.0", command)
        self.assertIn("--port 9100", command)
        self.assertTrue(command.endswith("--allow-remote"))

    def test_secrets_are_not_put_on_command_line(self):
        config = McpConfig(auth_token="s3cret", qmt_token="qmt-token")
        command = build_mcp_command(config)
        self.assertNotIn("s3cret", command)
        self.assertNotIn("qmt-token", command)


class BuildSpecTest(unittest.TestCase):
    def test_spec_uses_repo_root_and_current_interpreter(self):
        spec = build_mcp_spec(McpConfig())
        self.assertEqual(spec.cwd, str(REPO_ROOT))
        self.assertEqual(spec.python, sys.executable)
        self.assertEqual(spec.mode, "supervise")
        self.assertEqual(spec.log_file, default_log_file())

    def test_argv_uses_real_interpreter(self):
        spec = build_mcp_spec(McpConfig(port=9100), python=r"D:\py\python.exe")
        argv = build_command(spec)
        self.assertEqual(argv[0], r"D:\py\python.exe")
        self.assertEqual(argv[1:3], ["-m", "bigqmt.mcp"])
        self.assertIn("9100", argv)

    def test_overrides(self):
        spec = build_mcp_spec(
            McpConfig(), python="py.exe", cwd=r"D:\tmp", log_file=r"D:\tmp\mcp.log"
        )
        self.assertEqual(spec.python, "py.exe")
        self.assertEqual(spec.cwd, r"D:\tmp")
        self.assertEqual(spec.log_file, r"D:\tmp\mcp.log")

    def test_default_paths(self):
        self.assertTrue(default_log_file().endswith(str(Path("logs") / "mcp_server.log")))
        self.assertTrue(default_pid_file().endswith(str(Path("logs") / "mcp_server.pid")))


class BuildRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_file = str(Path(self.tmp.name) / "mcp.log")
        self.pid_file = str(Path(self.tmp.name) / "mcp.pid")

    def _runner(self, spawner=None, alive=True):
        spawner = spawner or FakeSpawner()
        runner = build_runner(
            McpConfig(port=9100),
            spawner=spawner,
            pid_alive=lambda pid: alive,
            watchdog=False,
            log_file=self.log_file,
            pid_file=self.pid_file,
        )
        return runner, spawner

    def test_start_spawns_process_and_writes_pid(self):
        runner, spawner = self._runner()
        self.assertTrue(runner.start(timeout=0))
        self.assertEqual(len(spawner.calls), 1)
        call = spawner.calls[0]
        self.assertEqual(call["argv"][1:3], ["-m", "bigqmt.mcp"])
        self.assertEqual(call["cwd"], str(REPO_ROOT))
        self.assertEqual(call["log_file"], self.log_file)
        self.assertTrue(runner.is_running())
        self.assertEqual(
            Path(self.pid_file).read_text(encoding="utf-8").strip(),
            str(spawner.last.pid),
        )

    def test_start_fails_when_process_exits_immediately(self):
        spawner = FakeSpawner(
            process_factory=lambda: FakeProcess(pid=1, alive=False, exit_code=3)
        )
        runner, _ = self._runner(spawner)
        self.assertFalse(runner.start(timeout=0.5))
        self.assertIn("exit=3", runner.status()["last_error"])

    def test_start_is_idempotent_and_pid_file_blocks_second_start(self):
        runner, spawner = self._runner()
        self.assertTrue(runner.start(timeout=0))
        self.assertTrue(runner.start(timeout=0))
        self.assertEqual(len(spawner.calls), 1)

    def test_stop_terminates_and_clears_pid(self):
        runner, spawner = self._runner()
        runner.start(timeout=0)
        self.assertTrue(runner.stop())
        self.assertIn("terminate", spawner.last.calls)
        self.assertFalse(Path(self.pid_file).exists())

    def test_stop_external_uses_pid_file(self):
        Path(self.pid_file).write_text("4321", encoding="utf-8")
        killed = []
        runner, _ = self._runner()
        with mock.patch("subprocess.run", side_effect=lambda *a, **k: killed.append(a[0])):
            runner.stop_external()
        self.assertTrue(killed, "应通过 taskkill 终止 PID")
        self.assertFalse(Path(self.pid_file).exists())


class ProbeQmtBackendTest(unittest.TestCase):
    def test_available(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def python_version(self):
                return {"python_version": "3.6.8"}

        with mock.patch.object(runner_mod, "QMTClient", FakeClient):
            ok, detail = runner_mod.probe_qmt_backend(McpConfig())
        self.assertTrue(ok)
        self.assertIn("3.6.8", detail)

    def test_unavailable_reports_error(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def python_version(self):
                raise QMTApiError("连接被拒绝", path="/api/sys/python_version")

        with mock.patch.object(runner_mod, "QMTClient", FakeClient):
            ok, detail = runner_mod.probe_qmt_backend(McpConfig())
        self.assertFalse(ok)
        self.assertIn("连接被拒绝", detail)


class CliMcpCommandTest(unittest.TestCase):
    """`bigqmt mcp ...` 子命令的调度测试。"""

    class StubRunner:
        def __init__(self, ok=True, running=False):
            self.ok = ok
            self.running = running
            self.calls = []
            self._pid_file = str(Path(tempfile.gettempdir()) / "bigqmt_mcp_test.pid")

        def status(self):
            return {
                "state": "running" if self.running else "idle",
                "mode": "supervise",
                "running": self.running,
                "pid": 4321 if self.running else None,
                "exit_code": None,
                "restart_count": 0,
                "last_error": None,
                "log_file": r"D:\tmp\mcp.log",
                "pid_file": self._pid_file,
                "command": "python -m bigqmt.mcp",
            }

        def is_running(self):
            return self.running

        def start(self, timeout=None):
            self.calls.append(("start", timeout))
            return self.ok

        def stop_external(self):
            self.calls.append(("stop_external", None))
            return True

    def _run(self, argv, runner, probe=(True, "ok")):
        from bigqmt.core.qmt import cli

        buf = io.StringIO()
        with mock.patch("bigqmt.mcp.runner.build_runner", return_value=runner):
            with mock.patch("bigqmt.mcp.runner.probe_qmt_backend", return_value=probe):
                with redirect_stdout(buf):
                    code = cli.main(argv)
        return code, buf.getvalue()

    def test_status(self):
        code, out = self._run(["mcp", "status"], self.StubRunner(running=True))
        self.assertEqual(code, 0)
        self.assertIn("mcp url", out)
        self.assertIn("mcp running     : True", out)
        self.assertIn("qmt api ok      : True", out)

    def test_status_reports_backend_down(self):
        code, out = self._run(
            ["mcp", "status"], self.StubRunner(running=True), probe=(False, "连接被拒绝")
        )
        self.assertEqual(code, 0)
        self.assertIn("qmt api ok      : False", out)
        self.assertIn("连接被拒绝", out)

    def test_start(self):
        runner = self.StubRunner()
        code, out = self._run(["mcp", "start"], runner)
        self.assertEqual(code, 0)
        self.assertIn("MCP 服务已启动", out)
        self.assertEqual(runner.calls, [("start", None)])

    def test_start_when_already_running_is_idempotent(self):
        runner = self.StubRunner(running=True)
        code, out = self._run(["mcp", "start"], runner)
        self.assertEqual(code, 0)
        self.assertIn("已在运行", out)
        self.assertEqual(runner.calls, [], "已在运行时不应重复拉起")

    def test_start_failure_returns_1(self):
        runner = self.StubRunner(ok=False)
        code, out = self._run(["mcp", "start"], runner)
        self.assertEqual(code, 1)
        self.assertIn("启动失败", out)

    def test_restart_stops_then_starts(self):
        runner = self.StubRunner(running=True)
        code, _ = self._run(["mcp", "restart"], runner)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [("stop_external", None), ("start", None)])

    def test_stop(self):
        runner = self.StubRunner()
        code, out = self._run(["mcp", "stop"], runner)
        self.assertEqual(code, 0)
        self.assertIn("已停止", out)
        self.assertEqual(runner.calls, [("stop_external", None)])

    def test_remote_bind_without_flag_is_rejected(self):
        runner = self.StubRunner()
        code, out = self._run(["mcp", "start", "--host", "0.0.0.0"], runner)
        self.assertEqual(code, 2)
        self.assertIn("配置错误", out)
        self.assertEqual(runner.calls, [])

    def test_remote_bind_with_flag_is_allowed(self):
        runner = self.StubRunner()
        code, _ = self._run(
            ["mcp", "start", "--host", "0.0.0.0", "--allow-remote"], runner
        )
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [("start", None)])

    def test_backend_down_warns_but_starts(self):
        runner = self.StubRunner()
        code, out = self._run(
            ["mcp", "start"], runner, probe=(False, "Read timed out")
        )
        self.assertEqual(code, 0)
        self.assertIn("警告", out)
        self.assertIn("HTTP API", out)
        self.assertEqual(runner.calls, [("start", None)])

    def test_skip_qmt_check_suppresses_probe(self):
        runner = self.StubRunner()
        # _run 默认让探测返回失败；带 --skip-qmt-check 时不应出现警告
        code, out = self._run(
            ["mcp", "start", "--skip-qmt-check"], runner, probe=(False, "x")
        )
        self.assertEqual(code, 0)
        self.assertNotIn("警告", out)
        self.assertEqual(runner.calls, [("start", None)])


if __name__ == "__main__":
    unittest.main()
