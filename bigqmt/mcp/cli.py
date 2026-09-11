"""`bigqmt-mcp` 命令行入口。

用法：
    python -m bigqmt.mcp                     # 默认 127.0.0.1:9000
    python -m bigqmt.mcp --port 9100
    python -m bigqmt.mcp --host 0.0.0.0 --allow-remote --auth-token s3cret

配置来源优先级：命令行 > 环境变量 / .env > 内置默认值。
CLI 会先把生效配置写回环境变量，再导入 `bigqmt.mcp.server`，
以保证模块级 FastMCP 实例读取到同一份配置。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional, Sequence

from bigqmt.mcp.config import McpConfig, McpConfigError, load_mcp_config

logger = logging.getLogger("bigqmt.mcp.cli")

#: McpConfig 字段 -> 环境变量名
ENV_KEYS = {
    "host": "QMT_MCP_HOST",
    "port": "QMT_MCP_PORT",
    "allow_remote": "QMT_MCP_ALLOW_REMOTE",
    "auth_token": "QMT_MCP_AUTH_TOKEN",
    "qmt_base_url": "QMT_HTTP_BASE_URL",
    "qmt_token": "QMT_HTTP_TOKEN",
    "request_timeout": "QMT_MCP_REQUEST_TIMEOUT",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bigqmt-mcp",
        description="BigQMT MCP 服务：把 QMT 行情/财务/资金流/账户查询能力暴露为 MCP 工具（只读）",
    )
    parser.add_argument("--host", help="监听地址（默认 127.0.0.1；非回环地址需 --allow-remote）")
    parser.add_argument("--port", type=int, help="监听端口（默认 9000）")
    parser.add_argument("--qmt-url", dest="qmt_url", help="QMT HTTP API 地址（默认 http://127.0.0.1:10086）")
    parser.add_argument("--qmt-token", dest="qmt_token", help="QMT HTTP API 的 X-Token")
    parser.add_argument(
        "--auth-token",
        dest="auth_token",
        help="启用 Bearer 鉴权，客户端需带 Authorization: Bearer <token>",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="允许绑定非回环地址（对局域网开放，建议同时设置 --auth-token）",
    )
    parser.add_argument(
        "--transport",
        default="http",
        choices=["http", "sse", "streamable-http"],
        help="传输协议（默认 http）",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（默认 INFO）",
    )
    return parser


def resolve_config(args: argparse.Namespace) -> McpConfig:
    """合并 CLI 参数与环境变量，并校验。"""
    updates = {}
    if args.host:
        updates["host"] = args.host
    if args.port:
        updates["port"] = args.port
    if args.qmt_url:
        updates["qmt_base_url"] = args.qmt_url
    if args.qmt_token:
        updates["qmt_token"] = args.qmt_token
    if args.auth_token:
        updates["auth_token"] = args.auth_token
    if args.allow_remote:
        updates["allow_remote"] = True

    config = load_mcp_config()
    if updates:
        config = config.with_updates(**updates)
    return config.validate()


def apply_to_env(config: McpConfig) -> None:
    """把生效配置写回环境变量，供 `bigqmt.mcp.server` 导入时读取。"""
    for field, key in ENV_KEYS.items():
        value = getattr(config, field)
        if isinstance(value, bool):
            os.environ[key] = "1" if value else "0"
        elif value is not None:
            os.environ[key] = str(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        config = resolve_config(args)
    except McpConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    apply_to_env(config)

    try:
        from bigqmt.mcp.server import mcp
    except ImportError as exc:  # fastmcp 缺失时给出明确指引
        print(
            f"无法导入 MCP 服务（缺少依赖？）: {exc}\n"
            "请安装：pip install -e .[mcp]",
            file=sys.stderr,
        )
        return 3

    middleware = None
    if config.auth_token:
        from starlette.middleware import Middleware

        from bigqmt.mcp.auth import BearerAuthMiddleware

        middleware = [Middleware(BearerAuthMiddleware, token=config.auth_token)]

    logger.info("QMT HTTP API: %s", config.qmt_base_url)
    logger.info(
        "MCP 服务监听 http://%s:%s（Bearer 鉴权: %s）",
        config.host,
        config.port,
        "开启" if config.auth_token else "关闭",
    )

    mcp.run(
        transport=args.transport,
        host=config.host,
        port=config.port,
        middleware=middleware,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
