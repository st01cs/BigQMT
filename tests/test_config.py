import os
import unittest
from unittest import mock

from bigqmt.config import QmtConfig, config_from_mapping, load_qmt_config


class ConfigFromMappingTest(unittest.TestCase):
    def test_defaults_when_empty(self):
        cfg = config_from_mapping({})
        self.assertIsNone(cfg.exe_path)
        self.assertIsNone(cfg.userdata_path)
        self.assertIsNone(cfg.account_id)
        self.assertIsNone(cfg.session_id)
        self.assertEqual(cfg.process_names, ("XtItClient.exe",))
        self.assertFalse(cfg.auto_start)
        self.assertTrue(cfg.auto_restart)
        self.assertEqual(cfg.max_restarts, 3)
        self.assertEqual(cfg.poll_interval, 20.0)
        self.assertEqual(cfg.login_timeout, 60)
        self.assertEqual(cfg.manual_login_wait, 120)

    def test_full_mapping(self):
        cfg = config_from_mapping(
            {
                "QMT_EXE_PATH": r"D:\QMT\bin.x64\XtItClient.exe",
                "QMT_USERDATA_PATH": r"D:\QMT\userdata_mini",
                "QMT_ACCOUNT_ID": "12345678",
                "QMT_PASSWORD": "secret",
                "QMT_SESSION_ID": "42",
                "QMT_PROCESS_NAMES": "XtItClient.exe, xtmini.exe",
                "QMT_AUTO_START": "true",
                "QMT_AUTO_RESTART": "0",
                "QMT_MAX_RESTARTS": "5",
                "QMT_POLL_INTERVAL": "10.5",
                "QMT_LOGIN_TIMEOUT": "90",
                "QMT_MANUAL_LOGIN_WAIT": "300",
            }
        )
        self.assertEqual(cfg.exe_path, r"D:\QMT\bin.x64\XtItClient.exe")
        self.assertEqual(cfg.userdata_path, r"D:\QMT\userdata_mini")
        self.assertEqual(cfg.account_id, "12345678")
        self.assertEqual(cfg.password, "secret")
        self.assertEqual(cfg.session_id, 42)
        self.assertEqual(cfg.process_names, ("XtItClient.exe", "xtmini.exe"))
        self.assertTrue(cfg.auto_start)
        self.assertFalse(cfg.auto_restart)
        self.assertEqual(cfg.max_restarts, 5)
        self.assertEqual(cfg.poll_interval, 10.5)
        self.assertEqual(cfg.login_timeout, 90)
        self.assertEqual(cfg.manual_login_wait, 300)

    def test_userdata_alias_qmt_data_dir(self):
        cfg = config_from_mapping({"QMT_DATA_DIR": r"D:\QMT\userdata"})
        self.assertEqual(cfg.userdata_path, r"D:\QMT\userdata")

    def test_userdata_path_precedence_over_alias(self):
        cfg = config_from_mapping(
            {
                "QMT_USERDATA_PATH": r"D:\A\userdata_mini",
                "QMT_DATA_DIR": r"D:\B\userdata",
            }
        )
        self.assertEqual(cfg.userdata_path, r"D:\A\userdata_mini")

    def test_invalid_bool_int_fall_back_to_defaults(self):
        cfg = config_from_mapping(
            {
                "QMT_AUTO_START": "not-a-bool",
                "QMT_MAX_RESTARTS": "abc",
                "QMT_SESSION_ID": "xyz",
            }
        )
        self.assertFalse(cfg.auto_start)
        self.assertEqual(cfg.max_restarts, 3)
        self.assertIsNone(cfg.session_id)


class LoadConfigTest(unittest.TestCase):
    def test_load_from_environment(self):
        with mock.patch.dict(
            os.environ,
            {"QMT_ACCOUNT_ID": "88888888", "QMT_AUTO_START": "1"},
            clear=True,
        ):
            cfg = load_qmt_config()
        self.assertEqual(cfg.account_id, "88888888")
        self.assertTrue(cfg.auto_start)


class WithUpdatesTest(unittest.TestCase):
    def test_immutable_copy(self):
        cfg = QmtConfig()
        updated = cfg.with_updates(auto_start=True)
        self.assertFalse(cfg.auto_start)
        self.assertTrue(updated.auto_start)


if __name__ == "__main__":
    unittest.main()
