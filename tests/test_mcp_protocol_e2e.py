"""MCP 协议级 E2E：对真实运行的服务做 initialize / tools/list / resources/read。

默认 skip（与仓库既有 E2E 约定一致）。启用方式：

    QMT_MCP_E2E=1 python -m unittest tests.test_mcp_protocol_e2e -v

可选覆盖地址：QMT_MCP_E2E_BASE（默认 http://127.0.0.1:9000/mcp），
启用 Bearer 时设置 QMT_MCP_E2E_TOKEN。

实现仅用标准库，不依赖 requests/fastmcp。
"""

import json
import os
import unittest
import urllib.error
import urllib.request

BASE_URL = os.environ.get("QMT_MCP_E2E_BASE", "http://127.0.0.1:9000/mcp")
AUTH_TOKEN = os.environ.get("QMT_MCP_E2E_TOKEN", "")
PROTOCOL = "2025-06-18"
ENABLED = os.environ.get("QMT_MCP_E2E") == "1"


def _post(payload, session=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["mcp-session-id"] = session
        headers["MCP-Protocol-Version"] = PROTOCOL
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    request = urllib.request.Request(
        BASE_URL, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


def _messages(body):
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    if not out and body.strip().startswith("{"):
        out.append(json.loads(body))
    return out


@unittest.skipUnless(ENABLED, "设置 QMT_MCP_E2E=1 并在服务运行时执行")
class McpProtocolE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status, headers, body = _post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "bigqmt-e2e", "version": "1.0"},
                },
            }
        )
        assert status == 200, f"initialize 失败: {status} {body[:200]}"
        cls.session = headers.get("mcp-session-id") or headers.get("MCP-Session-Id")
        cls.init_result = _messages(body)[0]["result"]
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, cls.session)

    def test_protocol_version_negotiated(self):
        self.assertIn("protocolVersion", self.init_result)
        self.assertTrue(self.session, "缺少 mcp-session-id 响应头")

    def test_tools_list_has_full_catalog(self):
        status, _, body = _post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            self.session,
        )
        self.assertEqual(status, 200)
        tools = _messages(body)[0]["result"]["tools"]
        self.assertEqual(len(tools), 88)
        self.assertIn("get_stock_name", {t["name"] for t in tools})

    def test_resources_readable(self):
        status, _, body = _post(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
             "params": {"uri": "qmt://info/pr_types"}},
            self.session,
        )
        self.assertEqual(status, 200)
        contents = _messages(body)[0]["result"]["contents"]
        self.assertIn("prType", contents[0]["text"])

    def test_stock_name_roundtrip(self):
        status, _, body = _post(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "get_stock_name", "arguments": {"stockcode": "600000.SH"}}},
            self.session,
        )
        self.assertEqual(status, 200)
        message = _messages(body)[0]
        if "error" in message:  # QMT 后端不可用时跳过，而非误报协议问题
            self.skipTest(f"QMT 后端不可用: {message['error']}")
        self.assertFalse(message["result"].get("isError"), message["result"])
        self.assertIn("600000.SH", json.dumps(message["result"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
