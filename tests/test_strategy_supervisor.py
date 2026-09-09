import tempfile
import unittest

from bigqmt.config import QmtConfig
from bigqmt.core.qmt import QmtManager, QmtState
from bigqmt.core.qmt._strategy import (
    StrategyMode,
    StrategyRunner,
    StrategySpec,
    StrategyState,
    StrategySupervisor,
)
from tests.test_strategy_runner import FakeSpawner


class FakeDriver:
    """可编排的假驱动（同 test_connection 简化版）。"""

    def __init__(self, process=False, logged=False, trading=False):
        self.process = process
        self.logged = logged
        self.trading = trading

    def is_process_running(self):
        return self.process

    def is_logged_in(self):
        return self.logged

    def trading_ready(self):
        return self.trading

    def start_client(self):
        self.process = True
        return True

    def login(self, timeout=None):
        self.logged = True
        return True

    def close_client(self):
        self.process = False
        self.logged = False
        return True


def _base_config(**overrides):
    values = dict(
        password="secret",
        auto_restart=True,
        max_restarts=2,
        login_timeout=5,
        poll_interval=20.0,
    )
    values.update(overrides)
    return QmtConfig(**values)


class StrategySupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _make_supervisor(self, driver, *, max_restarts=2):
        import time

        manager = QmtManager(
            config=_base_config(max_restarts=max_restarts),
            driver=driver,
            watchdog=False,
            sleep=lambda seconds: time.sleep(0.01),
        )
        runner = StrategyRunner(
            StrategySpec(
                command="python strategy.py",
                mode=StrategyMode.SUPERVISE.value,
                start_timeout=0,
                max_restarts=max_restarts,
            ),
            spawner=FakeSpawner(),
            watchdog=False,
            pid_file=f"{self.tmp.name}/strategy.pid",
        )
        return manager, runner, StrategySupervisor(manager, runner)

    def test_ready_starts_strategy_and_disconnect_stops_then_recovery_restarts(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        manager, runner, supervisor = self._make_supervisor(driver)

        # 首次就绪 -> 策略启动（监听器触发）
        self.assertTrue(supervisor.start(timeout=5))
        self.assertEqual(manager.state, QmtState.READY)
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertIsNotNone(runner.status()["pid"])

        spawner = runner._spawner
        self.assertEqual(len(spawner.calls), 1)

        # QMT 掉线 -> 策略被停止
        driver.process = False
        driver.logged = False
        manager._heartbeat_once()
        self.assertEqual(manager.state, QmtState.DISCONNECTED)
        self.assertEqual(runner.state, StrategyState.STOPPED)

        # QMT 恢复 -> 策略以全新进程重启
        driver.process = True
        driver.logged = True
        manager._heartbeat_once()
        self.assertEqual(manager.state, QmtState.READY)
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(len(spawner.calls), 2)

    def test_supervisor_start_when_manager_already_ready(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        manager = QmtManager(
            config=_base_config(),
            driver=driver,
            watchdog=False,
            sleep=lambda seconds: None,
        )
        self.assertTrue(manager.start(timeout=5))

        # 管理器已 READY 后再挂 supervisor（无状态迁移），显式 start 仍会拉起策略
        spawner = FakeSpawner()
        runner = StrategyRunner(
            StrategySpec(command="python strategy.py", start_timeout=0),
            spawner=spawner,
            watchdog=False,
            pid_file=f"{self.tmp.name}/strategy.pid",
        )
        supervisor = StrategySupervisor(manager, runner)
        self.assertTrue(supervisor.start(timeout=5))
        self.assertEqual(runner.state, StrategyState.RUNNING)
        self.assertEqual(len(spawner.calls), 1)

    def test_stop_stops_strategy_and_manager(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        manager, runner, supervisor = self._make_supervisor(driver)
        self.assertTrue(supervisor.start(timeout=5))
        self.assertTrue(supervisor.stop())
        self.assertEqual(manager.state, QmtState.STOPPED)
        self.assertEqual(runner.state, StrategyState.STOPPED)


if __name__ == "__main__":
    unittest.main()
