import sys
import unittest

from bigqmt.config import QmtConfig
from bigqmt.core.qmt._strategy import (
    StrategyConfigError,
    StrategyMode,
    StrategySpec,
    build_command,
    build_strategy_spec,
)


class BuildStrategySpecTest(unittest.TestCase):
    def test_disabled_without_command_is_ok(self):
        spec = build_strategy_spec(QmtConfig(strategy_enabled=False))
        self.assertFalse(spec.enabled)
        self.assertEqual(spec.mode, StrategyMode.SUPERVISE.value)

    def test_maps_config_fields(self):
        config = QmtConfig(
            strategy_enabled=True,
            strategy_cmd=r"python D:\strat\main.py --trade",
            strategy_python=r"D:\venv\python.exe",
            strategy_cwd=r"D:\strat",
            strategy_mode="once",
            strategy_max_restarts=7,
            strategy_log=r"D:\strat\logs\run.log",
            strategy_start_timeout=45,
            strategy_grace=3.5,
        )
        spec = build_strategy_spec(config)
        self.assertTrue(spec.enabled)
        self.assertEqual(spec.command, r"python D:\strat\main.py --trade")
        self.assertEqual(spec.python, r"D:\venv\python.exe")
        self.assertEqual(spec.cwd, r"D:\strat")
        self.assertEqual(spec.mode, "once")
        self.assertEqual(spec.max_restarts, 7)
        self.assertEqual(spec.log_file, r"D:\strat\logs\run.log")
        self.assertEqual(spec.start_timeout, 45)
        self.assertEqual(spec.grace_timeout, 3.5)

    def test_mode_case_and_whitespace_normalized(self):
        spec = build_strategy_spec(QmtConfig(strategy_mode="  ONCE  "))
        self.assertEqual(spec.mode, "once")

    def test_enabled_without_command_raises(self):
        with self.assertRaises(StrategyConfigError):
            build_strategy_spec(QmtConfig(strategy_enabled=True))

    def test_invalid_mode_raises(self):
        with self.assertRaises(StrategyConfigError):
            build_strategy_spec(QmtConfig(strategy_mode="foreground"))


class BuildCommandTest(unittest.TestCase):
    def test_replaces_bare_python_with_sys_executable(self):
        spec = StrategySpec(command="python main.py --trade")
        self.assertEqual(
            build_command(spec), [sys.executable, "main.py", "--trade"]
        )

    def test_uses_configured_python(self):
        spec = StrategySpec(command="python main.py", python=r"D:\venv\python.exe")
        self.assertEqual(build_command(spec), [r"D:\venv\python.exe", "main.py"])

    def test_keeps_explicit_python_path(self):
        spec = StrategySpec(command=r"D:\venv\python.exe main.py")
        self.assertEqual(build_command(spec), [r"D:\venv\python.exe", "main.py"])

    def test_keeps_windows_backslashes_outside_quotes(self):
        spec = StrategySpec(command=r"python D:\strat\main.py --opt C:\data")
        self.assertEqual(
            build_command(spec),
            [sys.executable, r"D:\strat\main.py", "--opt", r"C:\data"],
        )

    def test_quoted_argument_with_spaces(self):
        spec = StrategySpec(command=r'python "D:\my strat\main.py" --trade')
        self.assertEqual(
            build_command(spec),
            [sys.executable, r"D:\my strat\main.py", "--trade"],
        )

    def test_empty_command_raises(self):
        with self.assertRaises(StrategyConfigError):
            build_command(StrategySpec(command=None))
        with self.assertRaises(StrategyConfigError):
            build_command(StrategySpec(command="   "))


if __name__ == "__main__":
    unittest.main()
