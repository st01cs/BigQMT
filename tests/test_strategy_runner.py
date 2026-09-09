import os
import subprocess
import sys
import tempfile
import unittest

from bigqmt.core.qmt._strategy import (
    StrategyMode,
    StrategyRunner,
    StrategySpec,
    StrategyState,
)


class FakeProcess:
    """模拟子进程：可通过属性驱动退出/存活与 terminate 行为。"""

    def __init__(self, pid, alive=True, exit_code=None, dies_on_terminate=True):
        self.pid = pid
        self.alive = alive
        self.exit_code = exit_code
        self.dies_on_terminate = dies_on_terminate
        self.calls = []

    def poll(self):
        if self.alive:
            return None
        return self.exit_code if self.exit_code is not None else 0

    def terminate(self):
        self.calls.append("terminate")
        if self.dies_on_terminate:
            self.alive = False

    def kill(self):
        self.calls.append("kill")
        self.alive = False

    def wait(self, timeout=None):
        if not self.alive:
            return self.exit_code if self.exit_code is not None else 0
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)


class FakeSpawner:
    """记录每次拉起参数，并生产可编程的 FakeProcess。"""

    def __init__(self, process_factory=None):
        self.calls = []
        self.processes = []
        self._factory = process_factory

    def __call__(self, argv, cwd=None, log_file=None):
        self.calls.append({"argv": argv, "cwd": cwd, "log_file": log_file})
        if self._factory is not None:
            process = self._factory()
        else:
            process = FakeProcess(pid=1000 + len(self.calls))
        self.processes.append(process)
        return process

    @property
    def last(self):
        return self.processes[-1]


def _runner(
    spec_kwargs=None,
    *,
    pid_file=None,
    pid_alive=None,
    **runner_kwargs,
):
    """构造指向临时日志/PID 文件、默认禁用后台监督的 runner。"""
    spec = StrategySpec(**(spec_kwargs or {}))
    return StrategyRunner(
        spec,
        watchdog=False,
        pid_file=pid_file,
        pid_alive=pid_alive,
        **runner_kwargs,
    )


class StrategyRunnerStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid_file = os.path.join(self.tmp.name, "strategy.pid")

    def test_start_success_and_pid_file(self):
        spawner = FakeSpawner()
        runner = _runner(
            {
                "command": "python main.py --trade",
                "cwd": self.tmp.name,
                "mode": StrategyMode.SUPERVISE.value,
                "start_timeout": 0,
            },
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(len(spawner.calls), 1)
        # 裸 python 已替换为实际解释器
        self.assertEqual(spawner.calls[0]["argv"][0], sys.executable)
        self.assertEqual(spawner.calls[0]["argv"][1:], ["main.py", "--trade"])
        self.assertEqual(spawner.calls[0]["cwd"], self.tmp.name)
        self.assertIsNotNone(spawner.calls[0]["log_file"])
        # PID 文件已写入
        with open(self.pid_file, encoding="utf-8") as fh:
            self.assertEqual(int(fh.read().strip()), runner.status()["pid"])

    def test_start_idempotent_when_running(self):
        spawner = FakeSpawner()
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        self.assertTrue(runner.start())
        self.assertEqual(len(spawner.calls), 1)

    def test_start_spawn_failure_goes_error(self):
        def boom(argv, cwd=None, log_file=None):
            raise OSError("python not found")

        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=boom,
            pid_file=self.pid_file,
        )
        self.assertFalse(runner.start())
        self.assertEqual(runner.state, StrategyState.ERROR)
        self.assertIn("python not found", runner.last_error)

    def test_start_immediate_exit_during_confirm_is_failure(self):
        spawner = FakeSpawner(
            process_factory=lambda: FakeProcess(
                pid=1, alive=False, exit_code=2
            )
        )
        runner = _runner(
            {
                "command": "python main.py",
                "mode": StrategyMode.SUPERVISE.value,
                "start_timeout": 1,
            },
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertFalse(runner.start())
        self.assertEqual(runner.state, StrategyState.ERROR)
        self.assertEqual(runner.status()["exit_code"], 2)
        self.assertFalse(os.path.exists(self.pid_file))

    def test_start_refused_when_pid_file_live(self):
        with open(self.pid_file, "w", encoding="utf-8") as fh:
            fh.write("999")
        spawner = FakeSpawner()
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
            pid_alive=lambda pid: pid == 999,
        )
        self.assertFalse(runner.start())
        self.assertIn("已在运行", runner.last_error)
        self.assertEqual(len(spawner.calls), 0)


class StrategyRunnerSuperviseTest(unittest.TestCase):
    """supervise：崩溃自动重启，超出上限转 ERROR（手动驱动 check）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid_file = os.path.join(self.tmp.name, "strategy.pid")

    def test_crash_restarts_until_cap(self):
        spawner = FakeSpawner()
        runner = _runner(
            {
                "command": "python main.py",
                "mode": StrategyMode.SUPERVISE.value,
                "max_restarts": 2,
                "start_timeout": 0,
            },
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())

        # 第 1 次崩溃 -> 重启（restart_count=1，新进程）
        spawner.processes[0].alive = False
        runner.check()
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(runner.restart_count, 1)
        self.assertEqual(len(spawner.calls), 2)

        # 第 2 次崩溃 -> 重启（restart_count=2，仍在上限内）
        spawner.last.alive = False
        runner.check()
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(runner.restart_count, 2)
        self.assertEqual(len(spawner.calls), 3)

        # 第 3 次崩溃 -> 超过上限 -> ERROR
        spawner.last.alive = False
        runner.check()
        self.assertEqual(runner.state, StrategyState.ERROR)
        self.assertIn("超过重启上限", runner.last_error)
        self.assertFalse(os.path.exists(self.pid_file))

    def test_no_restart_while_still_alive(self):
        spawner = FakeSpawner()
        runner = _runner(
            {
                "command": "python main.py",
                "mode": StrategyMode.SUPERVISE.value,
                "start_timeout": 0,
            },
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        runner.check()
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(len(spawner.calls), 1)


class StrategyRunnerOnceTest(unittest.TestCase):
    """once：进程退出即完成，不自动重启。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid_file = os.path.join(self.tmp.name, "strategy.pid")

    def test_once_exit_stops_without_restart(self):
        spawner = FakeSpawner()
        runner = _runner(
            {
                "command": "python main.py",
                "mode": StrategyMode.ONCE.value,
                "start_timeout": 0,
            },
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        spawner.last.alive = False
        runner.check()
        self.assertEqual(runner.state, StrategyState.STOPPED)
        self.assertEqual(len(spawner.calls), 1)


class StrategyRunnerStopTest(unittest.TestCase):
    """停止顺序：先 terminate，超宽限仍存活再 kill。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pid_file = os.path.join(self.tmp.name, "strategy.pid")

    def test_stop_terminates_and_removes_pid(self):
        spawner = FakeSpawner(
            process_factory=lambda: FakeProcess(
                pid=7, alive=True, dies_on_terminate=True
            )
        )
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        self.assertTrue(runner.stop())
        self.assertEqual(runner.state, StrategyState.STOPPED)
        self.assertEqual(spawner.last.calls, ["terminate"])
        self.assertNotIn("kill", spawner.last.calls)
        self.assertFalse(os.path.exists(self.pid_file))

    def test_stop_kills_after_grace_timeout(self):
        spawner = FakeSpawner(
            process_factory=lambda: FakeProcess(
                pid=8, alive=True, dies_on_terminate=False
            )
        )
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        self.assertTrue(runner.stop(grace=0))
        self.assertEqual(runner.state, StrategyState.STOPPED)
        self.assertEqual(spawner.last.calls, ["terminate", "kill"])

    def test_restart_spawns_fresh_process(self):
        spawner = FakeSpawner()
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        self.assertTrue(runner.restart())
        self.assertEqual(len(spawner.calls), 2)
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(runner.restart_count, 0)

    def test_status_snapshot(self):
        spawner = FakeSpawner()
        runner = _runner(
            {"command": "python main.py", "start_timeout": 0},
            spawner=spawner,
            pid_file=self.pid_file,
        )
        self.assertTrue(runner.start())
        status = runner.status()
        self.assertEqual(status["state"], StrategyState.RUNNING.value)
        self.assertTrue(status["running"])
        self.assertIsNotNone(status["pid"])
        self.assertEqual(status["command"], "python main.py")
        self.assertEqual(status["pid_file"], self.pid_file)


if __name__ == "__main__":
    unittest.main()
