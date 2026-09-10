import os
import tempfile
import unittest
from unittest import mock

from bigqmt.config import (
    QmtConfig,
    config_from_mapping,
    load_qmt_config,
    parse_env_file,
)


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
        self.assertFalse(cfg.trading_required)
        self.assertEqual(cfg.max_restarts, 3)
        self.assertEqual(cfg.poll_interval, 20.0)
        self.assertEqual(cfg.login_timeout, 60)
        self.assertEqual(cfg.manual_login_wait, 120)
        self.assertEqual(cfg.login_action_delay, 0.5)
        self.assertEqual(cfg.login_type_interval, 0.05)

    def test_login_pacing_mapping(self):
        cfg = config_from_mapping(
            {"QMT_LOGIN_ACTION_DELAY": "0.8", "QMT_LOGIN_TYPE_INTERVAL": "0.12"}
        )
        self.assertEqual(cfg.login_action_delay, 0.8)
        self.assertEqual(cfg.login_type_interval, 0.12)

    def test_login_pacing_invalid_falls_back(self):
        cfg = config_from_mapping(
            {"QMT_LOGIN_ACTION_DELAY": "abc", "QMT_LOGIN_TYPE_INTERVAL": "x"}
        )
        self.assertEqual(cfg.login_action_delay, 0.5)
        self.assertEqual(cfg.login_type_interval, 0.05)

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
                "QMT_TRADING_REQUIRED": "true",
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
        self.assertTrue(cfg.trading_required)
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

    def test_strategy_defaults(self):
        cfg = config_from_mapping({})
        self.assertFalse(cfg.strategy_enabled)
        self.assertIsNone(cfg.strategy_cmd)
        self.assertIsNone(cfg.strategy_python)
        self.assertIsNone(cfg.strategy_cwd)
        self.assertEqual(cfg.strategy_mode, "supervise")
        self.assertEqual(cfg.strategy_max_restarts, 3)
        self.assertIsNone(cfg.strategy_log)
        self.assertEqual(cfg.strategy_start_timeout, 30)
        self.assertEqual(cfg.strategy_grace, 10.0)

    def test_strategy_full_mapping(self):
        cfg = config_from_mapping(
            {
                "QMT_STRATEGY_ENABLED": "true",
                "QMT_STRATEGY_CMD": r'python D:\strat\main.py --trade',
                "QMT_STRATEGY_PYTHON": r"D:\venv\python.exe",
                "QMT_STRATEGY_CWD": r"D:\strat",
                "QMT_STRATEGY_MODE": "once",
                "QMT_STRATEGY_MAX_RESTARTS": "7",
                "QMT_STRATEGY_LOG": r"D:\strat\logs\run.log",
                "QMT_STRATEGY_START_TIMEOUT": "45",
                "QMT_STRATEGY_GRACE": "3.5",
            }
        )
        self.assertTrue(cfg.strategy_enabled)
        self.assertEqual(cfg.strategy_cmd, r"python D:\strat\main.py --trade")
        self.assertEqual(cfg.strategy_python, r"D:\venv\python.exe")
        self.assertEqual(cfg.strategy_cwd, r"D:\strat")
        self.assertEqual(cfg.strategy_mode, "once")
        self.assertEqual(cfg.strategy_max_restarts, 7)
        self.assertEqual(cfg.strategy_log, r"D:\strat\logs\run.log")
        self.assertEqual(cfg.strategy_start_timeout, 45)
        self.assertEqual(cfg.strategy_grace, 3.5)

    def test_strategy_invalid_values_fall_back_to_defaults(self):
        cfg = config_from_mapping(
            {
                "QMT_STRATEGY_ENABLED": "not-a-bool",
                "QMT_STRATEGY_MAX_RESTARTS": "abc",
                "QMT_STRATEGY_GRACE": "x",
            }
        )
        self.assertFalse(cfg.strategy_enabled)
        self.assertEqual(cfg.strategy_max_restarts, 3)
        self.assertEqual(cfg.strategy_grace, 10.0)


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


class ParseEnvFileTest(unittest.TestCase):
    def test_parses_basic_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.path.join(tmp, ".env")
            with open(env, "w", encoding="utf-8") as fh:
                fh.write(
                    "# comment\n"
                    "QMT_ACCOUNT_ID=12345678\n"
                    'QMT_EXE_PATH="D:/QMT/XtItClient.exe"\n'
                    "\n"
                    "EMPTY_LINE_ABOVE=1\n"
                )
            values = parse_env_file(env)
        self.assertEqual(values["QMT_ACCOUNT_ID"], "12345678")
        self.assertEqual(values["QMT_EXE_PATH"], "D:/QMT/XtItClient.exe")
        self.assertEqual(values["EMPTY_LINE_ABOVE"], "1")
        self.assertNotIn("comment", values)

    def test_value_keeps_equals_and_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.path.join(tmp, ".env")
            with open(env, "w", encoding="utf-8") as fh:
                fh.write("KEY_WITH_EQ=A=B C\n")
            self.assertEqual(parse_env_file(env)["KEY_WITH_EQ"], "A=B C")


class WithUpdatesTest(unittest.TestCase):
    def test_immutable_copy(self):
        cfg = QmtConfig()
        updated = cfg.with_updates(auto_start=True)
        self.assertFalse(cfg.auto_start)
        self.assertTrue(updated.auto_start)


if __name__ == "__main__":
    unittest.main()
