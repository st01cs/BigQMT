import time
import unittest
from unittest import mock

import bigqmt.core.qmt._connection as connection_mod
from bigqmt.config import QmtConfig
from bigqmt.core.qmt import (
    QmtManager,
    QmtNotReadyError,
    QmtState,
    get_qmt_manager,
    require_ready,
)


class FakeDriver:
    """可编排的假驱动，用于状态迁移测试。"""

    def __init__(self, process=False, logged=False, trading=False):
        self.process = process
        self.logged = logged
        self.trading = trading
        self.start_calls = 0
        self.login_calls = 0
        self.close_calls = 0

    def is_process_running(self):
        return self.process

    def is_logged_in(self):
        return self.logged

    def trading_ready(self):
        return self.trading

    def start_client(self):
        self.start_calls += 1
        self.process = True
        return True

    def login(self, timeout=None):
        self.login_calls += 1
        self.logged = True
        return True

    def close_client(self):
        self.close_calls += 1
        self.process = False
        self.logged = False
        return True


class StuckLoginDriver(FakeDriver):
    """login() 永远无法登录成功的假驱动。"""

    def login(self, timeout=None):
        self.login_calls += 1
        return False


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


def _make_manager(driver, *, watchdog=False, config=None, sleep=None):
    return QmtManager(
        config=config or _base_config(),
        driver=driver,
        watchdog=watchdog,
        sleep=sleep if sleep is not None else (lambda seconds: time.sleep(0.01)),
    )


def _reset_singleton():
    connection_mod._manager = None


class QmtManagerTest(unittest.TestCase):
    def setUp(self):
        _reset_singleton()

    def tearDown(self):
        _reset_singleton()

    def test_initial_state_stopped(self):
        mgr = _make_manager(FakeDriver())
        self.assertEqual(mgr.state, QmtState.STOPPED)
        status = mgr.status()
        self.assertEqual(status["state"], "stopped")
        self.assertIn("config", status)
        self.assertFalse(status["process_running"])

    def test_start_transitions_to_ready(self):
        driver = FakeDriver(process=False, logged=False, trading=True)
        mgr = _make_manager(driver)
        self.assertTrue(mgr.start(timeout=5))
        self.assertEqual(mgr.state, QmtState.READY)
        self.assertEqual(driver.start_calls, 1)
        self.assertGreaterEqual(driver.login_calls, 1)
        self.assertIsNone(mgr.last_error)

    def test_start_when_already_ready_returns_true_without_relaunch(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        mgr = _make_manager(driver)
        self.assertTrue(mgr.start(timeout=5))
        self.assertTrue(mgr.start(timeout=5))
        self.assertEqual(driver.start_calls, 0)

    def test_start_fails_when_client_cannot_start(self):
        driver = FakeDriver(process=False, logged=False, trading=True)

        def broken_start():
            return False

        driver.start_client = broken_start
        mgr = _make_manager(driver)
        self.assertFalse(mgr.start(timeout=1))
        self.assertEqual(mgr.state, QmtState.ERROR)
        self.assertIsNotNone(mgr.last_error)

    def test_start_login_timeout_reaches_error(self):
        driver = StuckLoginDriver(process=True, logged=False, trading=True)
        mgr = _make_manager(driver)
        self.assertFalse(mgr.start(timeout=0.2))
        self.assertEqual(mgr.state, QmtState.ERROR)
        self.assertIn("登录", mgr.last_error or "")

    def test_start_waits_for_manual_login_without_password(self):
        driver = FakeDriver(process=True, logged=False, trading=True)
        config = _base_config(password=None)
        mgr = _make_manager(driver, config=config)
        self.assertFalse(mgr.start(timeout=0.2))
        self.assertEqual(mgr.state, QmtState.ERROR)
        self.assertIn("人工登录", mgr.last_error or "")

    def test_start_reports_trading_not_ready(self):
        driver = FakeDriver(process=True, logged=True, trading=False)
        config = _base_config(trading_required=True)
        mgr = _make_manager(driver, config=config)
        self.assertFalse(mgr.start(timeout=1))
        self.assertEqual(mgr.state, QmtState.LOGIN)
        self.assertIn("交易通道", mgr.last_error or "")

    def test_start_ready_without_trading_when_not_required(self):
        """默认数据/登录模式：登录成功即 READY，无需交易通道。"""
        driver = FakeDriver(process=True, logged=True, trading=False)
        config = _base_config(trading_required=False)
        mgr = _make_manager(driver, config=config)
        self.assertTrue(mgr.start(timeout=5))
        self.assertEqual(mgr.state, QmtState.READY)
        self.assertIsNone(mgr.last_error)

    def test_heartbeat_recovers_from_disconnect(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        mgr = _make_manager(driver)
        self.assertTrue(mgr.start(timeout=5))

        driver.process = False
        driver.logged = False
        mgr._heartbeat_once()
        self.assertEqual(mgr.state, QmtState.DISCONNECTED)
        self.assertEqual(mgr.status()["restart_count"], 1)

        driver.process = True
        driver.logged = True
        mgr._heartbeat_once()
        self.assertEqual(mgr.state, QmtState.READY)
        self.assertEqual(mgr.status()["restart_count"], 0)

    def test_heartbeat_fails_after_max_restarts(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        config = _base_config(max_restarts=2)
        mgr = _make_manager(driver, config=config)
        self.assertTrue(mgr.start(timeout=5))

        driver.process = False
        driver.logged = False
        # 恢复动作“无效”：启动/登录都不会真正修复状态
        driver.start_client = lambda: True
        driver.login = lambda timeout=None: False

        mgr._heartbeat_once()  # 第1次：DISCONNECTED，尝试1
        mgr._heartbeat_once()  # 第2次：仍断，尝试2
        self.assertEqual(mgr.state, QmtState.DISCONNECTED)
        mgr._heartbeat_once()  # 第3次：超过上限 -> ERROR
        self.assertEqual(mgr.state, QmtState.ERROR)
        self.assertIn("上限", mgr.last_error or "")

    def test_stop_sets_stopped_and_optional_close_client(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        mgr = _make_manager(driver)
        self.assertTrue(mgr.start(timeout=5))
        self.assertTrue(mgr.stop(close_client=True))
        self.assertEqual(mgr.state, QmtState.STOPPED)
        self.assertEqual(driver.close_calls, 1)
        self.assertTrue(mgr.stop())  # 幂等

    def test_restart_relaunches_after_close(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        mgr = _make_manager(driver)
        self.assertTrue(mgr.start(timeout=5))
        self.assertEqual(driver.start_calls, 0)
        self.assertTrue(mgr.restart(timeout=5, close_client=True))
        self.assertEqual(mgr.state, QmtState.READY)
        self.assertEqual(driver.start_calls, 1)

    def test_ensure_ready_respects_auto_start_flag(self):
        driver = FakeDriver(process=False, logged=False, trading=True)
        mgr = _make_manager(driver)
        self.assertFalse(mgr.ensure_ready(auto_start=False))
        self.assertEqual(mgr.state, QmtState.STOPPED)
        self.assertTrue(mgr.ensure_ready(auto_start=True))
        self.assertEqual(mgr.state, QmtState.READY)

    def test_watchdog_thread_starts_and_stops(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        config = _base_config(poll_interval=0.05)
        mgr = _make_manager(driver, watchdog=True, config=config)
        self.assertTrue(mgr.start(timeout=5))
        self.assertIsNotNone(mgr._watchdog_thread)
        self.assertTrue(mgr._watchdog_thread.is_alive())
        mgr.stop()
        self.assertFalse(mgr._watchdog_thread.is_alive())
        self.assertEqual(mgr.state, QmtState.STOPPED)


class RequireReadyDecoratorTest(unittest.TestCase):
    def setUp(self):
        _reset_singleton()

    def tearDown(self):
        _reset_singleton()

    def test_decorator_runs_when_ready(self):
        driver = FakeDriver(process=True, logged=True, trading=True)
        get_qmt_manager(config=_base_config(), driver=driver)

        @require_ready()
        def f():
            return 42

        self.assertEqual(f(), 42)

    def test_decorator_raises_when_not_ready(self):
        driver = StuckLoginDriver(process=True, logged=False, trading=True)
        get_qmt_manager(config=_base_config(login_timeout=1), driver=driver)

        @require_ready(auto_start=True, timeout=0.2)
        def f():
            return 42

        with self.assertRaises(QmtNotReadyError):
            f()


class ContextManagerTest(unittest.TestCase):
    def setUp(self):
        _reset_singleton()

    def tearDown(self):
        _reset_singleton()

    def test_context_enter_ready_and_exit_stopped(self):
        driver = FakeDriver(process=False, logged=False, trading=True)
        mgr = _make_manager(driver)
        with mgr as entered:
            self.assertIs(entered, mgr)
            self.assertEqual(mgr.state, QmtState.READY)
        self.assertEqual(mgr.state, QmtState.STOPPED)

    def test_context_enter_raises_when_cannot_ready(self):
        driver = StuckLoginDriver(process=True, logged=False, trading=True)
        config = _base_config(login_timeout=1)
        mgr = _make_manager(driver, config=config)
        with self.assertRaises(QmtNotReadyError):
            with mgr:
                pass


if __name__ == "__main__":
    unittest.main()
