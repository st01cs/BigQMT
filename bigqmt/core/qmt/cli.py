"""QMT 管理命令行入口。

用法：
    python -m bigqmt.core.qmt status|login|start|stop|restart
    python -m bigqmt.core.qmt start --strategy <cmd> [--strategy-mode once|supervise]
    python -m bigqmt.core.qmt strategy start|stop|status|restart [--strategy <cmd>]
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from bigqmt.config import QmtConfig, load_qmt_config
from ._connection import get_qmt_manager
from ._strategy import (
    StrategyConfigError,
    StrategyRunner,
    StrategySupervisor,
    build_strategy_spec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bigqmt-qmt", description="BigQMT QMT 生命周期管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看 QMT 状态")

    start = sub.add_parser("start", help="启动 QMT 至就绪（进程+登录+交易通道）")
    start.add_argument("--timeout", type=float, default=None, help="就绪等待超时（秒）")
    start.add_argument(
        "--strategy",
        metavar="CMD",
        default=None,
        help="就绪后自动运行的策略命令行（前台监督，Ctrl+C 退出）",
    )
    start.add_argument(
        "--strategy-mode",
        choices=["once", "supervise"],
        default=None,
        help="策略运行模式（默认取配置 QMT_STRATEGY_MODE）",
    )

    login = sub.add_parser("login", help="仅执行自动登录")
    login.add_argument("--timeout", type=float, default=None, help="登录超时（秒）")

    stop = sub.add_parser("stop", help="停止管理器（默认不关闭 QMT 客户端）")
    stop.add_argument("--close-client", action="store_true", help="同时关闭 QMT 客户端")

    restart = sub.add_parser("restart", help="重启 QMT")
    restart.add_argument("--timeout", type=float, default=None, help="就绪等待超时（秒）")
    restart.add_argument("--keep-client", action="store_true", help="重启时保留客户端进程")

    strat = sub.add_parser("strategy", help="管理自动运行的策略进程")
    strat_sub = strat.add_subparsers(dest="strategy_action", required=True)
    for action in ("start", "stop", "status", "restart"):
        p = strat_sub.add_parser(action, help=f"策略 {action}")
        p.add_argument("--strategy", metavar="CMD", default=None, help="策略命令行（覆盖配置）")
        p.add_argument(
            "--strategy-mode",
            choices=["once", "supervise"],
            default=None,
            help="策略运行模式（默认取配置 QMT_STRATEGY_MODE）",
        )
        p.add_argument("--timeout", type=float, default=None, help="就绪等待超时（秒）")

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


def _print_strategy_status(status: dict) -> None:
    print(f"QMT state       : {status['state']}")
    print(f"strategy state  : {status['strategy']['state']}")
    print(f"strategy running: {status['strategy']['running']}")
    print(f"strategy pid    : {status['strategy']['pid']}")
    print(f"strategy mode   : {status['strategy']['mode']}")
    print(f"restart_count   : {status['strategy']['restart_count']}")
    print(f"strategy error  : {status['strategy']['last_error']}")
    print(f"strategy log    : {status['strategy']['log_file']}")


def _config_with_strategy(args) -> QmtConfig:
    """在 .env 配置基础上叠加命令行指定的策略命令/模式。"""
    config = load_qmt_config()
    updates: dict = {}
    if getattr(args, "strategy", None):
        updates["strategy_enabled"] = True
        updates["strategy_cmd"] = args.strategy
    if getattr(args, "strategy_mode", None):
        updates["strategy_mode"] = args.strategy_mode
    return config.with_updates(**updates) if updates else config


def _build_supervisor(config: QmtConfig, manager=None) -> StrategySupervisor:
    manager = manager if manager is not None else get_qmt_manager(config=config)
    spec = build_strategy_spec(config)
    runner = StrategyRunner(spec)
    return StrategySupervisor(manager, runner)


def _run_foreground(supervisor: StrategySupervisor, once: bool) -> int:
    """前台跟随：once 等到策略退出；supervise 持续运行直到 Ctrl+C。"""
    print("[bigqmt] 策略运行中...（Ctrl+C 停止）" if not once else "[bigqmt] 策略运行中...")
    try:
        if once:
            supervisor.runner.wait()
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[bigqmt] 收到中断，正在停止策略与监督...")
    finally:
        supervisor.stop(close_client=False)
    return 0


def _cmd_start_with_strategy(args) -> int:
    config = _config_with_strategy(args)
    if not (config.strategy_enabled and config.strategy_cmd):
        print("未指定策略命令（--strategy 或 .env QMT_STRATEGY_CMD）")
        return 1
    try:
        supervisor = _build_supervisor(config)
    except StrategyConfigError as exc:
        print(f"[bigqmt] {exc}")
        return 1
    if not supervisor.start(timeout=args.timeout):
        print(f"[bigqmt] QMT 就绪/策略启动失败: {supervisor.status()['last_error']}")
        return 1
    print("[bigqmt] QMT READY，策略已启动")
    return _run_foreground(supervisor, once=config.strategy_mode == "once")


def _cmd_strategy(args) -> int:
    config = _config_with_strategy(args)
    enabled = config.strategy_enabled and bool(config.strategy_cmd)
    action = args.strategy_action

    if not enabled:
        if action in ("status", "stop"):
            print("[bigqmt] 未配置策略（QMT_STRATEGY_CMD）")
            return 0
        print("未指定策略命令（--strategy 或 .env QMT_STRATEGY_CMD）")
        return 1

    try:
        spec = build_strategy_spec(config)
    except StrategyConfigError as exc:
        print(f"[bigqmt] {exc}")
        return 1

    manager = get_qmt_manager(config=config)
    runner = StrategyRunner(spec)

    if action == "status":
        supervisor = StrategySupervisor(manager, runner)
        _print_strategy_status(supervisor.status())
        return 0

    if action == "stop":
        # 可能由独立进程调用：按 PID 文件整树终止
        runner.stop_external()
        print("[bigqmt] 策略已停止")
        return 0

    if action == "start":
        supervisor = _build_supervisor(config, manager=manager)
        if not supervisor.start(timeout=args.timeout):
            print(f"[bigqmt] QMT 就绪/策略启动失败: {supervisor.status()['last_error']}")
            return 1
        print("[bigqmt] QMT READY，策略已启动")
        return _run_foreground(supervisor, once=spec.mode == "once")

    if action == "restart":
        runner.stop_external()
        supervisor = _build_supervisor(config, manager=manager)
        if not supervisor.start(timeout=args.timeout):
            print(f"[bigqmt] QMT 就绪/策略启动失败: {supervisor.status()['last_error']}")
            return 1
        print("[bigqmt] 策略已重启")
        return _run_foreground(supervisor, once=spec.mode == "once")

    return 2


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "start" and args.strategy:
        return _cmd_start_with_strategy(args)
    if args.command == "strategy":
        return _cmd_strategy(args)

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
