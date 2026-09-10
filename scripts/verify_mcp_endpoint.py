"""MCP 端点手工验证脚本（仅标准库）。

对运行中的 MCP 服务做一次完整握手，可选抽查若干工具：

    python scripts/verify_mcp_endpoint.py
    python scripts/verify_mcp_endpoint.py --url http://127.0.0.1:9000/mcp
    python scripts/verify_mcp_endpoint.py --token <bearer> --call get_stock_name:{"stockcode":"600000.SH"}

退出码：0 = 协议层正常；1 = 协议层失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"


def _post(url, payload, session=None, token=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["mcp-session-id"] = session
        headers["MCP-Protocol-Version"] = PROTOCOL
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")
    except OSError as exc:
        print(f"无法连接 {url}: {exc}", file=sys.stderr)
        return 0, {}, ""


def _messages(body):
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    if not out and body.strip().startswith("{"):
        out.append(json.loads(body))
    return out


def _parse_call(text):
    name, _, raw = text.partition(":")
    return name.strip(), json.loads(raw) if raw.strip() else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 MCP 端点的协议与工具可用性")
    parser.add_argument("--url", default="http://127.0.0.1:9000/mcp")
    parser.add_argument("--token", default="", help="MCP 侧 Bearer token（服务启用鉴权时）")
    parser.add_argument(
        "--call",
        action="append",
        default=[],
        metavar="NAME:JSON",
        help="抽查工具，如 get_stock_name:{\"stockcode\":\"600000.SH\"}（可重复）",
    )
    args = parser.parse_args()

    status, headers, body = _post(
        args.url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "verify-mcp-endpoint", "version": "1.0"},
            },
        },
        token=args.token,
    )
    if status != 200:
        print(f"[FAIL] initialize HTTP {status}: {body[:200]}")
        return 1

    session = headers.get("mcp-session-id") or headers.get("MCP-Session-Id")
    result = _messages(body)[0]["result"]
    print(f"[OK] initialize  protocol={result.get('protocolVersion')} "
          f"server={result.get('serverInfo', {}).get('name')} "
          f"version={result.get('serverInfo', {}).get('version')}")
    print(f"     session={session}")

    _post(
        args.url,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session=session,
        token=args.token,
    )

    for req_id, method, key in (
        (2, "tools/list", "tools"),
        (3, "resources/list", "resources"),
        (4, "prompts/list", "prompts"),
    ):
        status, _, body = _post(
            args.url,
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": {}},
            session=session,
            token=args.token,
        )
        if status != 200:
            print(f"[FAIL] {method} HTTP {status}")
            return 1
        items = _messages(body)[0]["result"].get(key, [])
        names = [item.get("name") or item.get("uri") for item in items]
        print(f"[OK] {method}: {len(items)}  {names[:3]}{' ...' if len(names) > 3 else ''}")

    for index, raw in enumerate(args.call, start=10):
        name, arguments = _parse_call(raw)
        status, _, body = _post(
            args.url,
            {"jsonrpc": "2.0", "id": index, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
            session=session,
            token=args.token,
        )
        message = _messages(body)[0]
        if "error" in message:
            print(f"[FAIL] {name}: {json.dumps(message['error'], ensure_ascii=False)[:200]}")
            continue
        result = message["result"]
        payload = result.get("structuredContent")
        text = (
            json.dumps(payload, ensure_ascii=False)
            if payload is not None
            else (result.get("content") or [{}])[0].get("text", "")
        )
        flag = "FAIL" if result.get("isError") else "OK"
        print(f"[{flag}] tools/call {name}: {' '.join(text.split())[:220]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
