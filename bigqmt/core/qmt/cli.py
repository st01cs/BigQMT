"""QMT 管理命令行入口。

用法：
    python -m bigqmt.core.qmt status|login|start|stop|restart
"""

from __future__ import annotations

import argparse
import sys

from ._connection import get_qmt_manager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bigqmt-qmt", description="BigQMT QMT 生命周期管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看 QMT 状态")

    start = sub.add_parser("start", help="启动 QMT 至就绪（进程+登录+交易通道）")
    start.add_argument("--timeout", type=float, default=None, help="就绪等待超时（秒）")

    login = sub.add_parser("login", help="仅执行自动登录")
    login.add_argument("--timeout", type=float, default=None, help="登录超时（秒）")

    stop = sub.add_parser("stop", help="停止管理器（默认不关闭 QMT 客户端）")
    stop.add_argument("--close-client", action="store_true", help="同时关闭 QMT 客户端")

    restart = sub.add_parser("restart", help="重启 QMT")
    restart.add_argument("--timeout", type=float, default=None, help="就绪等待超时（秒）")
    restart.add_argument("--keep-client", action="store_true", help="重启时保留客户端进程")

    return parser


def _print_status(status: dict) -> None:
    print(f"state           : {status['state']}")
    print(f"process_running : {status['process_running']}")
    print(f"logged_in       : {status['logged_in']}")
    print(f"trading_ready   : {status['trading_ready']}")
    print(f"can_login       : {status['can_login']}")
    print(f"restart_count   : {status['restart_count']}")
    print(f"last_error      : {status['last_error']}")
    cfg = status["config"]
    print(f"exe_path        : {cfg['exe_path']}")
    print(f"userdata_path   : {cfg['userdata_path']}")
    print(f"account_id      : {cfg['account_id']}")


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = get_qmt_manager()

    if args.command == "status":
        _print_status(manager.status())
        return 0

    if args.command == "start":
        return 0 if manager.start(timeout=args.timeout) else 1

    if args.command == "login":
        return 0 if manager.login(timeout=args.timeout) else 1

    if args.command == "stop":
        return 0 if manager.stop(close_client=args.close_client) else 1

    if args.command == "restart":
        return 0 if manager.restart(
            timeout=args.timeout, close_client=not args.keep_client
        ) else 1

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_parser", "main"]
