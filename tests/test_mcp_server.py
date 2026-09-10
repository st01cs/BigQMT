"""MCP Server 契约测试。

`fastmcp` 属于可选依赖（extra `mcp`），未安装时整组用例 skip，
以保证默认环境（仅 python-dotenv）也能跑通测试套件。
"""

import asyncio
import inspect
import json
import unittest

try:
    import fastmcp  # noqa: F401

    HAS_FASTMCP = True
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    HAS_FASTMCP = False

if HAS_FASTMCP:
    from bigqmt.mcp import server as mcp_server
    from bigqmt.mcp.client import QMTApiError, set_client

#: 工具集基线（迁移 53 + 资金流 3 + 只读查询 51，去除 3 个交易工具）
EXPECTED_TOOLS = {
    "bsm_iv", "bsm_price", "get_ETF_list", "get_account_status",
    "get_all_subscription", "get_assure_contract", "get_available_funds",
    "get_back_test_index", "get_bar_timetag", "get_bvol", "get_close_price",
    "get_commission", "get_context_info", "get_contract_expire_date",
    "get_contract_multiplier", "get_date_location", "get_debt_contract",
    "get_divid_factors", "get_enable_short_contract", "get_etf_info",
    "get_etf_iopv", "get_ext_data", "get_ext_data_rank", "get_factor_data",
    "get_factor_rank", "get_factor_value", "get_finance", "get_financial_data",
    "get_float_caps", "get_his_contract_list", "get_his_index_data",
    "get_his_st_data", "get_history_data", "get_hkt_details",
    "get_hkt_statistics", "get_holder_num", "get_industry",
    "get_industry_name_of_stock", "get_instrument_detail", "get_ipo_data",
    "get_largecap", "get_last_close", "get_last_order_id", "get_last_volume",
    "get_local_data", "get_longhubang", "get_main_contract", "get_market_data",
    "get_market_data_ex_ori", "get_market_extended", "get_midcap",
    "get_net_value", "get_new_purchase_limit", "get_north_finance_change",
    "get_open_date", "get_option_detail_data", "get_option_iv",
    "get_option_list", "get_option_undl", "get_option_undl_data",
    "get_order_deal", "get_order_status", "get_portfolio_info", "get_positions",
    "get_product_asset_value", "get_product_init_share", "get_product_share",
    "get_raw_financial_data", "get_realtime_quote", "get_risk_free_rate",
    "get_sector", "get_slippage",
    "get_smallcap", "get_stock_list_in_sector", "get_stock_name",
    "get_stock_type", "get_svol", "get_tick_timetag", "get_top10_share_holder",
    "get_total_assets", "get_total_share", "get_trade_detail_data",
    "get_trading_dates", "get_turn_over_rate", "get_turnover_rate",
    "get_value_by_order_id", "get_weight_in_index", "is_fund", "is_future",
    "is_last_bar", "is_new_bar", "is_sector_stock", "is_stock",
    "is_suspended_stock", "is_typed_stock", "load_stk_list",
    "load_stk_vol_list", "stockcode_in_rzrk", "subscribe_quote",
    "subscribe_whole_quote", "timetag_to_datetime", "unsubscribe_quote",
}

#: 明确不允许出现在 MCP 工具集里的交易类接口
FORBIDDEN_TOOLS = {
    "buy_stock", "sell_stock", "cancel_all_orders", "passorder",
    "algo_passorder", "smart_algo_passorder", "order_lots", "order_value",
    "order_percent", "order_target_value", "order_target_percent",
    "order_shares", "futures_buy_open", "futures_sell_open",
    "cancel_task", "pause_task", "resume_task", "do_order",
}

EXPECTED_RESOURCES = {
    "qmt://info/version",
    "qmt://info/pr_types",
    "qmt://info/finance_fields",
}


class _RecordingClient:
    """记录调用的 QMT 客户端替身。"""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def _req(self, method, path, **kwargs):
        self.calls.append(("_req", method, path, kwargs))
        if self.raises is not None:
            raise self.raises
        return {"ok": True}

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.raises is not None:
                raise self.raises
            return {"ok": True}

        return _record


@unittest.skipUnless(HAS_FASTMCP, "需要 fastmcp（pip install -e .[mcp]）")
class McpRegistryTest(unittest.TestCase):
    @staticmethod
    def _resolve(value):
        """fastmcp 4.x 的 list_* 返回协程，这里统一等待。"""
        return asyncio.run(value) if inspect.isawaitable(value) else value

    def test_expected_tool_set_is_registered(self):
        names = {tool.name for tool in self._resolve(mcp_server.mcp.list_tools())}
        self.assertEqual(names, EXPECTED_TOOLS)
        self.assertEqual(len(names), 102)

    def test_no_trading_tools_are_registered(self):
        names = {tool.name for tool in self._resolve(mcp_server.mcp.list_tools())}
        self.assertFalse(names & FORBIDDEN_TOOLS, names & FORBIDDEN_TOOLS)
        for name in names:
            self.assertNotIn("order_target", name)
            self.assertNotIn("passorder", name)

    def test_expected_resources_are_registered(self):
        uris = {str(r.uri) for r in self._resolve(mcp_server.mcp.list_resources())}
        self.assertEqual(uris, EXPECTED_RESOURCES)

    def test_server_metadata_matches_config(self):
        self.assertEqual(mcp_server.mcp.name, mcp_server.MCP_CONFIG.name)
        self.assertEqual(mcp_server.MCP_CONFIG.version, "1.0.0")

    def test_tools_expose_input_schemas(self):
        for tool in self._resolve(mcp_server.mcp.list_tools()):
            schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
            self.assertIsInstance(schema, dict, tool.name)
            self.assertEqual(schema.get("type"), "object", tool.name)
            self.assertIn("properties", schema, tool.name)

    def test_tool_parameter_names_are_locked(self):
        schemas = {
            tool.name: (getattr(tool, "parameters", None) or {})
            for tool in self._resolve(mcp_server.mcp.list_tools())
        }
        self.assertEqual(
            schemas["get_stock_name"]["required"], ["stockcode"]
        )
        self.assertEqual(
            sorted(schemas["get_realtime_quote"]["required"]), ["stocks"]
        )
        self.assertEqual(sorted(schemas["get_trading_dates"]["properties"]),
                         ["count", "end_date", "market", "period", "start_date", "stockcode"])

    def test_finance_fields_resource_lists_tables(self):
        payload = json.loads(mcp_server.get_finance_fields_info())
        tables = payload["tables"]
        self.assertEqual(
            sorted(tables),
            ["ASHAREBALANCESHEET", "ASHARECASHFLOW", "ASHAREINCOME",
             "CAPITALSTRUCTURE", "PERSHAREINDEX"],
        )
        self.assertEqual(
            tables["ASHAREINCOME"]["字段"]["net_profit_incl_min_int_inc"], "净利润"
        )
        self.assertEqual(tables["CAPITALSTRUCTURE"]["字段"]["total_capital"], "总股本")

    def test_financial_tool_docstring_documents_field_format(self):
        tools = {tool.name: tool for tool in self._resolve(mcp_server.mcp.list_tools())}
        description = tools["get_financial_data"].parameters["properties"]["fieldList"]["description"]
        self.assertIn("表名.字段名", description)
        self.assertIn("qmt://info/finance_fields", description)


@unittest.skipUnless(HAS_FASTMCP, "需要 fastmcp（pip install -e .[mcp]）")
class McpToolContractTest(unittest.TestCase):
    """锁定 6 项接口契约修复，防止回归。"""

    def setUp(self):
        self.client = _RecordingClient()
        set_client(self.client)

    def tearDown(self):
        set_client(None)

    def test_instrument_detail_uses_backend_route(self):
        mcp_server.get_instrument_detail("600000.SH")
        method, path = self.client.calls[0][1], self.client.calls[0][2]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/data/instrumentdetail")

    def test_realtime_quote_delegates_to_full_tick(self):
        mcp_server.get_realtime_quote(["600000.SH"])
        self.assertEqual(self.client.calls[0][0], "get_full_tick")
        self.assertEqual(self.client.calls[0][1], (["600000.SH"],))

    def test_market_extended_delegates_with_stock_code(self):
        mcp_server.get_market_extended(["600000.SH"], period="1d", fields="close")
        name, kwargs = self.client.calls[0][0], self.client.calls[0][2]
        self.assertEqual(name, "get_market_data_ex")
        self.assertEqual(kwargs["stock_code"], ["600000.SH"])
        self.assertEqual(kwargs["fields"], "close")

    def test_market_data_delegates_with_stock_code(self):
        mcp_server.get_market_data(["600000.SH"], ["close"])
        name, kwargs = self.client.calls[0][0], self.client.calls[0][2]
        self.assertEqual(name, "get_market_data")
        self.assertEqual(kwargs["stock_code"], ["600000.SH"])
        self.assertEqual(kwargs["fields"], ["close"])

    def test_trading_dates_resolves_market_from_stock_code(self):
        mcp_server.get_trading_dates("600000.SH", count=5)
        kwargs = self.client.calls[0][2]
        self.assertEqual(kwargs["market"], "SH")
        self.assertEqual(kwargs["count"], 5)

    def test_trading_dates_accepts_explicit_market(self):
        mcp_server.get_trading_dates("000001.SZ", market="BJ")
        self.assertEqual(self.client.calls[0][2]["market"], "BJ")

    def test_resolve_market_fallbacks(self):
        resolve = mcp_server._resolve_market
        self.assertEqual(resolve("SZ"), "SZ")
        self.assertEqual(resolve("000001.SZ"), "SZ")
        self.assertEqual(resolve("600000.SH"), "SH")
        self.assertEqual(resolve("weird"), "SH")
        self.assertEqual(resolve("weird", "sz"), "SZ")

    def test_backend_error_propagates(self):
        set_client(_RecordingClient(raises=QMTApiError("boom", status_code=500)))
        with self.assertRaises(QMTApiError):
            mcp_server.get_total_assets()

    def test_north_finance_change_delegates(self):
        mcp_server.get_north_finance_change("1d")
        self.assertEqual(self.client.calls[0][0], "get_north_finance_change")
        self.assertEqual(self.client.calls[0][1], ("1d",))

    def test_hkt_tools_delegate(self):
        mcp_server.get_hkt_statistics("601899.SH")
        mcp_server.get_hkt_details("601899.SH")
        self.assertEqual(self.client.calls[0][0], "get_hkt_statistics")
        self.assertEqual(self.client.calls[0][1], ("601899.SH",))
        self.assertEqual(self.client.calls[1][0], "get_hkt_details")
        self.assertEqual(self.client.calls[1][1], ("601899.SH",))


@unittest.skipUnless(HAS_FASTMCP, "需要 fastmcp（pip install -e .[mcp]）")
class McpResourceTest(unittest.TestCase):
    def tearDown(self):
        set_client(None)

    def test_version_resource_reports_backend_failure(self):
        set_client(_RecordingClient(raises=QMTApiError("后端不可用")))
        payload = mcp_server.get_server_info()
        self.assertIn('"qmt_available": false', payload)
        self.assertIn("后端不可用", payload)

    def test_version_resource_reports_backend_ok(self):
        set_client(_RecordingClient())
        payload = mcp_server.get_server_info()
        self.assertIn('"qmt_available": true', payload)


if __name__ == "__main__":
    unittest.main()
