import unittest

from bigqmt.mcp.client import QMTClient, QMTApiError, as_csv, get_client, set_client
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
        client.cancel_all_orders("stock")
        paths = [call["url"].rsplit("10086", 1)[-1] for call in session.calls]
        self.assertEqual(
            paths,
            [
                "/api/holding",
                "/api/money/total",
                "/api/money/available",
                "/api/order/status",
                "/api/order/cancel_all",
            ],
        )

    def test_buy_sell_payload(self):
        session = _FakeSession()
        client = _client(session)
        client.buy_stock("600000.SH", 9.5, 100)
        client.sell_stock("600000.SH", 9.6, 200, pr_type=5)
        buy = session.calls[0]["kwargs"]["json"]
        sell = session.calls[1]["kwargs"]["json"]
        self.assertEqual(buy, {"stock": "600000.SH", "price": 9.5, "volume": 100, "prType": 11})
        self.assertEqual(sell, {"stock": "600000.SH", "price": 9.6, "volume": 200, "prType": 5})
        self.assertTrue(session.calls[0]["url"].endswith("/api/order/buy"))
        self.assertTrue(session.calls[1]["url"].endswith("/api/order/sell"))

    def test_python_version_uses_get(self):
        session = _FakeSession()
        _client(session).python_version()
        self.assertEqual(session.calls[0]["method"], "GET")
        self.assertTrue(session.calls[0]["url"].endswith("/api/sys/python_version"))


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
        self.assertIn("connection refused", str(ctx.exception))

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
