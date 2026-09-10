"""QMT 侧 HTTP 服务脚本的账号配置与启动自检测试。

`bigqmt/service/http.py` 是部署到 QMT（内置 Python 3.6）的 Tornado 脚本，
本机没有 tornado，因此这里注入最小 stub 后按文件加载，直接验证其中的纯逻辑。
"""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

SERVICE_FILE = Path(__file__).resolve().parents[1] / "bigqmt" / "service" / "http.py"


class _StubRequestHandler:
    pass


class _StubHTTPError(Exception):
    def __init__(self, status_code=500, log_message=None):
        super().__init__(log_message)
        self.status_code = status_code
        self.log_message = log_message


class _StubApplication:
    def __init__(self, routes=None, **kwargs):
        self.routes = routes or []
        self.ContextInfo = None
        self.accountID = None

    def listen(self, *args, **kwargs):
        pass


class _StubIOLoop:
    @staticmethod
    def current():
        return _StubIOLoop()

    def start(self):
        pass


def _install_tornado_stub():
    if "tornado.web" in sys.modules:
        return
    try:  # 真实 tornado 可用时优先使用
        import tornado.ioloop  # noqa: F401
        import tornado.web  # noqa: F401

        return
    except ImportError:
        pass
    tornado = types.ModuleType("tornado")
    web = types.ModuleType("tornado.web")
    ioloop = types.ModuleType("tornado.ioloop")
    web.Application = _StubApplication
    web.RequestHandler = _StubRequestHandler
    web.HTTPError = _StubHTTPError
    ioloop.IOLoop = _StubIOLoop
    tornado.web = web
    tornado.ioloop = ioloop
    sys.modules["tornado"] = tornado
    sys.modules["tornado.web"] = web
    sys.modules["tornado.ioloop"] = ioloop


def load_service_module(env=None):
    """按文件加载服务脚本；env 为 None 时不改环境变量。"""
    _install_tornado_stub()
    saved = {}
    if env is not None:
        for key in ("QMT_ACCOUNT_ID", "QMT_HTTP_TOKEN", "QMT_HTTP_PORT"):
            saved[key] = os.environ.pop(key, None)
        for key, value in env.items():
            os.environ[key] = value
    try:
        spec = importlib.util.spec_from_file_location("qmt_service_http", SERVICE_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if env is not None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return module


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class AccountIdResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_env_value_is_used(self):
        self.assertEqual(
            self.module.resolve_account_id({"QMT_ACCOUNT_ID": " 8887920826 "}),
            "8887920826",
        )

    def test_placeholder_is_treated_as_missing(self):
        for placeholder in ("你的QMT账号", "YOUR_ACCOUNT_ID", " your_qmt_account "):
            self.assertEqual(
                self.module.resolve_account_id({"QMT_ACCOUNT_ID": placeholder}), ""
            )

    def test_missing_env_returns_empty(self):
        self.assertEqual(self.module.resolve_account_id({}), "")

    def test_module_level_account_id_comes_from_env(self):
        module = load_service_module({"QMT_ACCOUNT_ID": "8887920826"})
        self.assertEqual(module.ACCOUNT_ID, "8887920826")

    def test_module_level_account_id_is_blank_without_env(self):
        module = load_service_module({"QMT_ACCOUNT_ID": ""})
        self.assertEqual(module.ACCOUNT_ID, "")


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class AccountValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_valid_account_passes(self):
        ok, message = self.module.check_account_id("8887920826")
        self.assertTrue(ok, message)

    def test_empty_and_placeholder_fail(self):
        for value in ("", None, "你的QMT账号"):
            ok, message = self.module.check_account_id(value)
            self.assertFalse(ok)
            self.assertTrue(message)

    def test_non_digit_account_fails(self):
        ok, _ = self.module.check_account_id("8887-920826")
        self.assertFalse(ok)

    def test_mask_keeps_last_four(self):
        self.assertEqual(self.module.mask_account("8887920826"), "******0826")
        self.assertEqual(self.module.mask_account("1234"), "****")
        self.assertEqual(self.module.mask_account(""), "")


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class TokenAndPortTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_token_default_and_override(self):
        self.assertEqual(self.module.resolve_token({}), "123456789")
        self.assertEqual(self.module.resolve_token({"QMT_HTTP_TOKEN": " abc "}), "abc")

    def test_port_default_and_override(self):
        self.assertEqual(self.module.resolve_port({}), 10086)
        self.assertEqual(self.module.resolve_port({"QMT_HTTP_PORT": "10087"}), 10087)

    def test_invalid_port_falls_back(self):
        for value in ("abc", "0", "70000", ""):
            self.assertEqual(
                self.module.resolve_port({"QMT_HTTP_PORT": value}), 10086, value
            )


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class AccountStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_unconfigured_account_reports_hint(self):
        status = self.module.account_status("")
        self.assertFalse(status["configured"])
        self.assertFalse(status["reachable"])
        self.assertIn("hint", status)
        self.assertEqual(status["account_id"], "")

    def test_configured_but_unreachable_outside_qmt(self):
        status = self.module.account_status("8887920826")
        self.assertTrue(status["configured"])
        self.assertFalse(status["reachable"])
        self.assertIn("取不到资金数据", status["message"])

    def test_reachable_when_trade_api_returns_account(self):
        class _Info:
            m_dBalance = 123456.789
            m_dAvailable = 1000.5

        original = self.module.__dict__.get("get_trade_detail_data")
        self.module.__dict__["get_trade_detail_data"] = lambda *a: [_Info()]
        try:
            status = self.module.account_status("8887920826")
        finally:
            if original is None:
                self.module.__dict__.pop("get_trade_detail_data", None)
            else:
                self.module.__dict__["get_trade_detail_data"] = original
        self.assertTrue(status["reachable"])
        self.assertEqual(status["total_money"], 123456.79)
        self.assertEqual(status["available_money"], 1000.5)
        self.assertEqual(status["account_id"], "******0826")


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class RequireAccountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def _handler(self, account):
        return type("H", (), {"acc": lambda self: account})()

    def test_unconfigured_account_raises_503(self):
        with self.assertRaises(Exception) as ctx:
            self.module.require_account(self._handler(""))
        self.assertEqual(getattr(ctx.exception, "status_code", None), 503)
        self.assertIn("QMT_ACCOUNT_ID", str(ctx.exception))

    def test_configured_account_passes(self):
        self.assertFalse(self.module.require_account(self._handler("8887920826")))


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class StartupSelfCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_self_check_logs_failure_when_account_missing(self):
        module = load_service_module({"QMT_ACCOUNT_ID": ""})
        with self.assertLogs(module.logger.name, level="INFO") as captured:
            module.log_startup_self_check()
        text = "\n".join(captured.output)
        self.assertIn("启动自检", text)
        self.assertIn("[FAIL]", text)
        self.assertIn("QMT_ACCOUNT_ID", text)

    def test_self_check_logs_warning_when_probe_unavailable(self):
        module = load_service_module({"QMT_ACCOUNT_ID": "8887920826"})
        with self.assertLogs(module.logger.name, level="INFO") as captured:
            module.log_startup_self_check()
        text = "\n".join(captured.output)
        self.assertIn("账号格式校验通过", text)
        self.assertIn("暂未取到资金数据", text)

    def test_self_check_logs_ok_when_probe_succeeds(self):
        module = load_service_module({"QMT_ACCOUNT_ID": "8887920826"})

        class _Info:
            m_dBalance = 42.0

        module.__dict__["get_trade_detail_data"] = lambda *a: [_Info()]
        with self.assertLogs(module.logger.name, level="INFO") as captured:
            module.log_startup_self_check()
        text = "\n".join(captured.output)
        self.assertIn("资金账号连通", text)


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class RouteRegistrationTest(unittest.TestCase):
    def test_account_status_route_registered(self):
        module = load_service_module()
        patterns = {route[0] for route in module.make_app().routes}
        self.assertIn("/api/sys/account_status", patterns)
        self.assertIn("/api/sys/python_version", patterns)
        self.assertIn("/api/money/total", patterns)


if __name__ == "__main__":
    unittest.main()
