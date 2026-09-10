import unittest
from pathlib import Path
from unittest import mock

import bigqmt.core.qmt._connection as connection_mod
from bigqmt.config import QmtConfig
from bigqmt.core.qmt._connection import DefaultQmtDriver
from bigqmt.core.qmt._paths import QmtLocations


def _locations(exe=True):
    return QmtLocations(
        install_dir=Path("D:/QMT"),
        userdata_dir=Path("D:/QMT/userdata_mini") if exe else None,
        exe_path=Path("D:/QMT/bin.x64/XtItClient.exe") if exe else None,
    )


class StubAutoLogin:
    def __init__(self, logged=True, login_ok=True):
        self.logged = logged
        self.login_ok = login_ok
        self.login_calls = 0
        self.last_error = None

    def is_logged_in(self):
        return self.logged

    def login(self, timeout=None):
        self.login_calls += 1
        self.logged = self.login_ok
        return self.login_ok


class DefaultDriverAutoLoginTest(unittest.TestCase):
    def test_without_password_no_auto_login(self):
        driver = DefaultQmtDriver(
            QmtConfig(password=None), _locations()
        )
        self.assertIsNone(driver._auto_login)
        self.assertFalse(driver.login(timeout=5))
        self.assertIn("QMT_PASSWORD", driver.last_error or "")

    def test_with_password_but_without_exe_no_auto_login(self):
        driver = DefaultQmtDriver(
            QmtConfig(password="secret", exe_path=None),
            _locations(exe=False),
        )
        self.assertIsNone(driver._auto_login)
        self.assertFalse(driver.login(timeout=5))
        self.assertIn("可执行文件", driver.last_error or "")

    def test_login_delegates_to_auto_login(self):
        driver = DefaultQmtDriver(
            QmtConfig(password="secret"), _locations()
        )
        stub = StubAutoLogin(logged=False, login_ok=True)
        driver._auto_login = stub
        self.assertTrue(driver.login(timeout=5))
        self.assertEqual(stub.login_calls, 1)

    def test_login_failure_sets_last_error(self):
        driver = DefaultQmtDriver(
            QmtConfig(password="secret"), _locations()
        )
        stub = StubAutoLogin(logged=False, login_ok=False)
        stub.last_error = "验证码需要人工处理"
        driver._auto_login = stub
        self.assertFalse(driver.login(timeout=5))
        self.assertIn("验证码", driver.last_error or "")

    def test_is_logged_in_delegates_when_xtdata_unavailable(self):
        driver = DefaultQmtDriver(
            QmtConfig(password="secret"), _locations()
        )
        stub = StubAutoLogin(logged=True)
        driver._auto_login = stub
        with mock.patch.object(driver, "_xtdata_connected", return_value=False):
            self.assertTrue(driver.is_logged_in())

    def test_login_pacing_config_passed_to_auto_login(self):
        config = QmtConfig(
            password="secret", login_action_delay=0.9, login_type_interval=0.11
        )
        with mock.patch.object(connection_mod, "NativeQmtAutoLogin") as fake_cls:
            DefaultQmtDriver(config, _locations())
        fake_cls.assert_called_once()
        kwargs = fake_cls.call_args.kwargs
        self.assertEqual(kwargs["action_delay"], 0.9)
        self.assertEqual(kwargs["type_interval"], 0.11)


if __name__ == "__main__":
    unittest.main()
