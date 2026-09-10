"""QMT 侧 HTTP 服务脚本的账号配置与启动自检测试。

`bigqmt/service/http.py` 是部署到 QMT（内置 Python 3.6）的 Tornado 脚本，
本机没有 tornado，因此这里注入最小 stub 后按文件加载，直接验证其中的纯逻辑。
"""

import json
import unittest

from tests.service_http_stub import SERVICE_FILE, load_service_module


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
        self.assertIn("/api/data/north_finance_change", patterns)
        self.assertIn("/api/data/hkt_statistics", patterns)
        self.assertIn("/api/data/hkt_details", patterns)


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class FundFlowHandlerTest(unittest.TestCase):
    """北向资金/港通三个 handler 的行为。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def _post(self, handler_cls, body, ctx):
        captured = {}

        class FakeHandler:
            request = type("Req", (), {"body": json.dumps(body).encode()})()

            def ctx(self):
                return ctx

            def write(self, payload):
                captured["payload"] = payload

        handler_cls.post(FakeHandler())
        return json.loads(captured["payload"])

    def test_north_finance_change_ok(self):
        class Ctx:
            def get_north_finance_change(self, period):
                return {"20260910": -1234.5}

        payload = self._post(
            self.module.NorthFinanceChangeHandler, {"period": "1d"}, Ctx()
        )
        self.assertEqual(payload["period"], "1d")
        self.assertIn("20260910", payload["data"])

    def test_empty_result_raises_503(self):
        class Ctx:
            def get_north_finance_change(self, period):
                return {}

        with self.assertRaises(Exception) as ctx:
            self._post(self.module.NorthFinanceChangeHandler, {}, Ctx())
        self.assertEqual(getattr(ctx.exception, "status_code", None), 503)

    def test_hkt_handlers_receive_stock_code(self):
        seen = {}

        class Ctx:
            def get_hkt_statistics(self, code):
                seen["stat"] = code
                return {"hold": 1}

            def get_hkt_details(self, code):
                seen["detail"] = code
                return [{"20260910": 1}]

        self._post(self.module.HktStatisticsHandler, {"stock_code": "601899.SH"}, Ctx())
        self._post(self.module.HktDetailsHandler, {"stock_code": "601899.SH"}, Ctx())
        self.assertEqual(seen, {"stat": "601899.SH", "detail": "601899.SH"})


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class WriteErrorTest(unittest.TestCase):
    """错误响应必须带上 raise HTTPError 时给的说明，而不是默认 reason。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def _call_write_error(self, error, status=500, reason="Service Unavailable"):
        captured = {}

        class Handler:
            _reason = reason

            def finish(self, payload):
                captured["payload"] = payload

        self.module.BaseHandler.write_error(
            Handler(), status, exc_info=(type(error), error, None)
        )
        return json.loads(captured["payload"])

    def test_custom_message_is_surfaced(self):
        error = self.module.HTTPError(503, "资金账号未配置：请设置 QMT_ACCOUNT_ID")
        payload = self._call_write_error(error, status=503)
        self.assertEqual(payload["status_code"], 503)
        self.assertIn("QMT_ACCOUNT_ID", payload["error"])
        self.assertNotEqual(payload["error"], "Service Unavailable")

    def test_falls_back_to_reason_without_exception(self):
        captured = {}

        class Handler:
            _reason = "Unauthorized"

            def finish(self, payload):
                captured["payload"] = payload

        self.module.BaseHandler.write_error(Handler(), 401)
        self.assertEqual(json.loads(captured["payload"])["error"], "Unauthorized")

    def test_blank_log_message_falls_back(self):
        error = self.module.HTTPError(400, "")
        payload = self._call_write_error(error, status=400, reason="Bad Request")
        self.assertEqual(payload["error"], "Bad Request")

    def test_name_error_is_explained(self):
        error = NameError("name 'get_open_date' is not defined")
        payload = self._call_write_error(error, status=500)
        self.assertIn("QMT 接口在当前环境不可用", payload["error"])


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class NormalizeDate8Test(unittest.TestCase):
    """QMT 的 get_turnover_rate / get_top10_share_holder 要求 8 位日期。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_empty_uses_default(self):
        self.assertEqual(self.module.normalize_date8("", "19720101"), "19720101")
        self.assertEqual(self.module.normalize_date8(None, "22010101"), "22010101")

    def test_separators_are_stripped(self):
        for value in ("2024-01-01", "2024/01/01", "2024.01.01", "20240101"):
            self.assertEqual(self.module.normalize_date8(value, "x"), "20240101", value)

    def test_invalid_values_fall_back(self):
        for value in ("2024-1-1", "abc", "202401011", "2024"):
            self.assertEqual(self.module.normalize_date8(value, "def"), "def", value)


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class PortInUseTest(unittest.TestCase):
    """启动前检测端口占用（QMT 停止策略后套接字可能泄漏）。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_closed_port_is_free(self):
        self.assertFalse(self.module.is_port_in_use(1))  # 1 号端口不会有服务监听

    def test_open_port_is_detected(self):
        import socket

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            self.assertTrue(self.module.is_port_in_use(port))
        finally:
            server.close()

    def test_invalid_input_is_safe(self):
        self.assertFalse(self.module.is_port_in_use("not-a-port"))


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class SanitizeJsonTest(unittest.TestCase):
    """NaN/Infinity 必须转成 null，否则响应不是合法 JSON。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_service_module()

    def test_nan_and_inf_become_none(self):
        sanitize = self.module.sanitize_json
        self.assertIsNone(sanitize(float("nan")))
        self.assertIsNone(sanitize(float("inf")))
        self.assertIsNone(sanitize(float("-inf")))

    def test_finite_values_kept(self):
        self.assertEqual(self.module.sanitize_json(1.5), 1.5)
        self.assertEqual(self.module.sanitize_json(0), 0)

    def test_nested_structures(self):
        payload = {"a": [1.0, float("nan")], "b": {"c": float("inf")}}
        self.assertEqual(
            self.module.sanitize_json(payload), {"a": [1.0, None], "b": {"c": None}}
        )

    def test_result_serialises_as_valid_json(self):
        payload = self.module.sanitize_json({"value": float("nan")})
        text = json.dumps(payload)
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text), {"value": None})

    def test_query_handler_sanitises_payload(self):
        captured = {}

        class FakeHandler:
            request = type("Req", (), {"body": json.dumps({"method": "get_holder_num", "args": [["601899.SH"]]}).encode()})()

            def ctx(self):
                class Ctx:
                    def get_holder_num(self, codes):
                        return float("nan")

                return Ctx()

            def write(self, payload):
                captured["payload"] = payload

        with self.assertRaises(Exception) as ctx:
            self.module.QueryHandler.post(FakeHandler())
        # NaN 被清理后视为空结果，返回 503 而不是裸 NaN
        self.assertEqual(getattr(ctx.exception, "status_code", None), 503)


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class HandlerCallStyleTest(unittest.TestCase):
    """回归防护：这几个 handler 必须走 ContextInfo 方法，而不是未注入的全局函数。"""

    @classmethod
    def setUpClass(cls):
        cls.source = SERVICE_FILE.read_text(encoding="utf-8")

    def test_fixed_handlers_use_context_methods(self):
        self.assertNotIn("safe_call(get_open_date", self.source)
        self.assertNotIn("safe_call(get_top10_share_holder", self.source)
        self.assertIn("self.ctx().get_open_date", self.source)
        self.assertIn("self.ctx().get_top10_share_holder", self.source)
        self.assertIn("self.ctx().get_turnover_rate", self.source)

    def test_turnover_rate_dates_are_normalised(self):
        self.assertIn("normalize_date8(data.get('startTime'", self.source)
        self.assertIn("normalize_date8(data.get('endTime'", self.source)


if __name__ == "__main__":
    unittest.main()
