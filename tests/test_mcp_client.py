import unittest

from bigqmt.mcp.client import (
    QMTClient,
    QMTApiError,
    as_csv,
    describe_request_error,
    get_client,
    set_client,
)
from bigqmt.mcp.config import McpConfig


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text or str(self._payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """记录请求并返回预设响应的 requests.Session 替身。"""

    def __init__(self, response=None):
        self.headers = {}
        self.calls = []
        self.response = response or _FakeResponse({})

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append(
            {"method": method, "url": url, "timeout": timeout, "kwargs": kwargs}
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(session):
    return QMTClient(
        base_url="http://127.0.0.1:10086",
        token="tok",
        timeout=5,
        session=session,
        config=McpConfig(),
    )


class AsCsvTest(unittest.TestCase):
    def test_list_is_joined(self):
        self.assertEqual(as_csv(["600000.SH", "000001.SZ"]), "600000.SH,000001.SZ")

    def test_string_passthrough_and_strip(self):
        self.assertEqual(as_csv(" 600000.SH "), "600000.SH")

    def test_none_and_empty(self):
        self.assertEqual(as_csv(None), "")
        self.assertEqual(as_csv([]), "")

    def test_tuple_and_scalar(self):
        self.assertEqual(as_csv(("600000.SH",)), "600000.SH")
        self.assertEqual(as_csv(600000), "600000")


class QMTClientRequestTest(unittest.TestCase):
    def test_full_tick_sends_comma_string(self):
        session = _FakeSession()
        _client(session).get_full_tick(["600000.SH", "000001.SZ"])
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "http://127.0.0.1:10086/api/data/full_tick")
        self.assertEqual(call["kwargs"]["json"], {"stocks": "600000.SH,000001.SZ"})

    def test_market_data_ex_uses_stock_code_key(self):
        session = _FakeSession()
        _client(session).get_market_data_ex(["600000.SH"], period="1d", fields="close")
        payload = session.calls[0]["kwargs"]["json"]
        self.assertEqual(payload["stock_code"], "600000.SH")
        self.assertNotIn("stocks", payload)
        self.assertEqual(payload["period"], "1d")
        self.assertEqual(payload["fields"], "close")

    def test_market_data_joins_fields_list(self):
        session = _FakeSession()
        _client(session).get_market_data(["600000.SH"], ["close", "open"])
        payload = session.calls[0]["kwargs"]["json"]
        self.assertEqual(payload["stock_code"], "600000.SH")
        self.assertEqual(payload["fields"], "close,open")

    def test_trading_dates_passes_market(self):
        session = _FakeSession()
        _client(session).get_trading_dates(market="SZ", count=5)
        payload = session.calls[0]["kwargs"]["json"]
        self.assertEqual(payload["stockcode"], "SZ")
        self.assertEqual(payload["count"], 5)

    def test_account_and_order_paths(self):
        session = _FakeSession()
        client = _client(session)
        client.get_holding("stock")
        client.get_total_money("stock")
        client.get_available_money("stock")
        client.get_order_status("stock")
        client.get_order_deal("stock")
        paths = [call["url"].rsplit("10086", 1)[-1] for call in session.calls]
        self.assertEqual(
            paths,
            [
                "/api/holding",
                "/api/money/total",
                "/api/money/available",
                "/api/order/status",
                "/api/order/deal",
            ],
        )

    def test_readonly_trade_queries(self):
        session = _FakeSession()
        client = _client(session)
        client.get_trade_detail_data("stock", "position")
        client.get_last_order_id("stock")
        client.get_value_by_order_id("12345")
        client.get_ipo_data()
        client.get_new_purchase_limit()
        client.get_debt_contract()
        client.get_assure_contract()
        client.get_enable_short_contract()
        paths = [call["url"].rsplit("10086", 1)[-1] for call in session.calls]
        self.assertEqual(
            paths,
            [
                "/api/trade/trade_detail_data",
                "/api/trade/last_order_id",
                "/api/trade/value_by_order_id",
                "/api/trade/ipo_data",
                "/api/trade/new_purchase_limit",
                "/api/trade/debt_contract",
                "/api/trade/assure_contract",
                "/api/trade/enable_short_contract",
            ],
        )
        self.assertEqual(
            session.calls[0]["kwargs"]["json"],
            {"account": "stock", "datatype": "position"},
        )
        self.assertEqual(
            session.calls[2]["kwargs"]["json"],
            {"orderId": "12345", "accountType": "stock", "datatype": "ORDER"},
        )

    def test_trading_methods_are_gone(self):
        for name in ("buy_stock", "sell_stock", "cancel_all_orders", "passorder"):
            self.assertFalse(hasattr(QMTClient, name), name)

    def test_query_ctx_payload(self):
        session = _FakeSession()
        _client(session).query_ctx("get_last_close", "601899.SH")
        self.assertTrue(session.calls[0]["url"].endswith("/api/data/query"))
        self.assertEqual(
            session.calls[0]["kwargs"]["json"],
            {"method": "get_last_close", "args": ["601899.SH"], "kwargs": {}},
        )

    def test_ext_check_and_context_paths(self):
        session = _FakeSession()
        client = _client(session)
        client.get_ext_data("EP_X", "601899.SH", -1)
        client.get_factor_value("ROE", "601899.SH")
        client.is_last_bar()
        client.get_industry_name_of_stock("SW", "601899.SH")
        client.get_account_status()
        client.get_context_value("period")
        paths = [call["url"].rsplit("10086", 1)[-1] for call in session.calls]
        self.assertEqual(
            paths,
            [
                "/api/ext/ext_data",
                "/api/ext/get_factor_value",
                "/api/check/is_last_bar",
                "/api/check/get_industry_name_of_stock",
                "/api/sys/account_status",
                "/api/context/period",
            ],
        )
        self.assertEqual(
            session.calls[0]["kwargs"]["json"],
            {"extdataname": "EP_X", "stockcode": "601899.SH", "deviation": -1},
        )
        self.assertEqual(session.calls[2]["method"], "GET")

    def test_python_version_uses_get(self):
        session = _FakeSession()
        _client(session).python_version()
        self.assertEqual(session.calls[0]["method"], "GET")
        self.assertTrue(session.calls[0]["url"].endswith("/api/sys/python_version"))

    def test_fund_flow_endpoints_and_keys(self):
        session = _FakeSession()
        client = _client(session)
        client.get_north_finance_change("1d")
        client.get_hkt_statistics("601899.SH")
        client.get_hkt_details("601899.SH")
        paths = [call["url"].rsplit("10086", 1)[-1] for call in session.calls]
        self.assertEqual(
            paths,
            [
                "/api/data/north_finance_change",
                "/api/data/hkt_statistics",
                "/api/data/hkt_details",
            ],
        )
        self.assertEqual(session.calls[0]["kwargs"]["json"], {"period": "1d"})
        self.assertEqual(session.calls[1]["kwargs"]["json"], {"stock_code": "601899.SH"})
        self.assertEqual(session.calls[2]["kwargs"]["json"], {"stock_code": "601899.SH"})


class QMTClientErrorTest(unittest.TestCase):
    def test_http_error_raises_with_status(self):
        session = _FakeSession(_FakeResponse({"error": "boom"}, status_code=500))
        with self.assertRaises(QMTApiError) as ctx:
            _client(session).get_total_money()
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.path, "/api/money/total")
        self.assertIn("500", str(ctx.exception))

    def test_network_error_raises(self):
        session = _FakeSession(RuntimeError("connection refused"))
        with self.assertRaises(QMTApiError) as ctx:
            _client(session).get_holding()
        self.assertIsNone(ctx.exception.status_code)
        self.assertIn("无法连接", str(ctx.exception))
        self.assertIn("127.0.0.1:10086", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_timeout_message_is_concise(self):
        session = _FakeSession(
            RuntimeError("HTTPConnectionPool(host='127.0.0.1', port=10086): Read timed out. (read timeout=10)")
        )
        with self.assertRaises(QMTApiError) as ctx:
            _client(session).python_version()
        self.assertIn("读取超时", str(ctx.exception))
        self.assertLess(len(str(ctx.exception)), 120)

    def test_describe_request_error_buckets(self):
        self.assertIn("无法连接", describe_request_error(RuntimeError("Connection refused")))
        self.assertIn("无法连接", describe_request_error(RuntimeError("Max retries exceeded with url")))
        self.assertIn("读取超时", describe_request_error(RuntimeError("Read timed out. (read timeout=10)")))
        self.assertEqual(
            describe_request_error(RuntimeError("  something   odd  ")), "something odd"
        )

    def test_non_json_response_raises(self):
        session = _FakeSession(_FakeResponse(ValueError("bad json"), text="<html>500</html>"))
        with self.assertRaises(QMTApiError) as ctx:
            _client(session).python_version()
        self.assertIn("JSON", str(ctx.exception))

    def test_error_payload_is_serialisable(self):
        err = QMTApiError("boom", method="POST", path="/api/x", status_code=404)
        self.assertEqual(
            err.as_dict(),
            {"error": "boom", "method": "POST", "path": "/api/x", "status_code": 404},
        )


class QMTClientSingletonTest(unittest.TestCase):
    def tearDown(self):
        set_client(None)

    def test_get_client_is_singleton(self):
        first = get_client(McpConfig(qmt_base_url="http://127.0.0.1:10086"))
        second = get_client(McpConfig(qmt_base_url="http://127.0.0.1:9999"))
        self.assertIs(first, second)
        self.assertEqual(first.base, "http://127.0.0.1:10086")

    def test_set_client_overrides(self):
        custom = QMTClient(config=McpConfig(), session=_FakeSession())
        set_client(custom)
        self.assertIs(get_client(), custom)


if __name__ == "__main__":
    unittest.main()
