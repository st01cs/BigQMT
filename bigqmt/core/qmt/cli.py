"""QMT 管理命令行入口。

用法：
    python -m bigqmt.core.qmt status|login|start|stop|restart
    python -m bigqmt.core.qmt start --strategy <cmd> [--strategy-mode once|supervise]
    python -m bigqmt.core.qmt strategy start|stop|status|restart [--strategy <cmd>]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
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

    mcp_svc = sub.add_parser("mcp", help="管理 MCP 服务进程（bigqmt.mcp）")
    mcp_sub = mcp_svc.add_subparsers(dest="mcp_action", required=True)
    for action in ("start", "stop", "status", "restart"):
        p = mcp_sub.add_parser(action, help=f"MCP 服务 {action}")
        p.add_argument("--host", default=None, help="MCP 监听地址（覆盖配置）")
        p.add_argument("--port", type=int, default=None, help="MCP 监听端口（覆盖配置）")
        p.add_argument("--python", default=None, help="运行 MCP 服务的解释器（默认当前解释器）")
        p.add_argument(
            "--auth-token",
            dest="auth_token",
            default=None,
            help="启用 Bearer 鉴权；通过环境变量传给子进程，不出现在命令行",
        )
        p.add_argument("--allow-remote", action="store_true", help="允许绑定非回环地址")
        p.add_argument("--timeout", type=float, default=None, help="启动确认超时（秒）")
        p.add_argument("--skip-qmt-check", action="store_true", help="跳过 QMT 就绪状态提示")

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


def _shorten(text, limit: int = 120) -> str:
    """把长错误信息压缩成一行，便于 CLI 阅读。"""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _read_pid_file(pid_file) -> Optional[int]:
    try:
        return int(Path(pid_file).read_text(encoding="utf-8").strip())
    except (OSError, ValueError, TypeError):
        return None


def _listening_pids(port: int) -> list:
    """返回监听指定端口的 PID 列表（netstat 解析，Windows）。"""
    try:
        completed = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if not parts[1].endswith(":%d" % port):
            continue
        try:
            pids.append(int(parts[4]))
        except ValueError:
            continue
    return sorted(set(pids))


def _free_port(port: int) -> tuple:
    """清理仍占用端口且不是本进程的遗留进程，返回 (已终止, 未能终止) 两个 PID 列表。

    Windows 下 venv 的 python.exe 是启动器，会再拉起真正的解释器进程；
    仅按 PID 文件 taskkill /T 有时杀不到被重新挂载的子进程，
    结果端口仍被占用、新实例绑定失败（WinError 10048）。
    """
    killed = []
    failed = []
    for pid in _listening_pids(port):
        if pid == os.getpid():
            continue
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
            )
            if getattr(completed, "returncode", 1) == 0:
                killed.append(pid)
            else:
                failed.append(pid)
        except (OSError, subprocess.SubprocessError):
            failed.append(pid)
    return killed, failed


def _warn_if_backend_down(config) -> bool:
    """探测 QMT 侧 HTTP API；不可用时给出可操作的提示。返回是否可用。"""
    from bigqmt.mcp.runner import probe_qmt_backend

    ok, detail = probe_qmt_backend(config)
    if ok:
        print(f"[bigqmt] QMT HTTP API 可用（{config.qmt_base_url}）")
        return True
    print(
        f"[bigqmt] 警告：QMT HTTP API（{config.qmt_base_url}）不可用：{_shorten(detail)}"
    )
    print(
        "[bigqmt]       请确认 QMT 已登录，且 QMT 内的 HTTP API 策略（"
        "bigqmt/service/http.py）正在运行，否则行情/账户类工具会失败。"
    )
    return False


def _print_mcp_status(runner, config) -> None:
    """打印 MCP 服务状态（本进程未持有子进程时回退到 PID 文件）。"""
    from bigqmt.mcp.runner import probe_qmt_backend

    status = runner.status()
    running = runner.is_running()
    pid = status["pid"]
    if pid is None and running:
        pid = _read_pid_file(status["pid_file"])
    backend_ok, backend_detail = probe_qmt_backend(config)
    print(f"mcp url         : http://{config.host}:{config.port}/mcp")
    print(f"mcp state       : {status['state']}")
    print(f"mcp running     : {running}")
    print(f"mcp pid         : {pid}")
    print(f"mcp auth        : {'Bearer' if config.auth_token else 'off'}")
    print(f"qmt backend     : {config.qmt_base_url}")
    print(
        f"qmt api ok      : {backend_ok}"
        + ("" if backend_ok else f"（{_shorten(backend_detail)}）")
    )
    print(f"mcp log         : {status['log_file']}")
    print(f"last_error      : {status['last_error']}")


def _cmd_mcp(args) -> int:
    """管理 MCP 服务进程（bigqmt.mcp）。"""
    from bigqmt.mcp.config import McpConfigError, load_mcp_config
    from bigqmt.mcp.runner import build_runner

    action = args.mcp_action
    config = load_mcp_config()
    updates: dict = {}
    if args.host:
        updates["host"] = args.host
    if args.port:
        updates["port"] = args.port
    if args.allow_remote:
        updates["allow_remote"] = True
    if args.auth_token:
        updates["auth_token"] = args.auth_token
    if updates:
        config = config.with_updates(**updates)

    try:
        config.validate()
    except McpConfigError as exc:
        print(f"[bigqmt] MCP 配置错误: {exc}")
        return 2

    if args.auth_token:
        # 子进程按环境变量读取，避免 token 出现在命令行
        os.environ["QMT_MCP_AUTH_TOKEN"] = args.auth_token

    runner = build_runner(config, python=args.python)

    if action == "status":
        _print_mcp_status(runner, config)
        return 0

    if action == "stop":
        runner.stop_external()
        killed, failed = _free_port(config.port)
        if killed:
            print(f"[bigqmt] 已清理仍占用端口 {config.port} 的遗留进程: {killed}")
        if failed:
            print(
                f"[bigqmt] 警告：无法终止占用端口 {config.port} 的进程 {failed}"
                "（可能权限不足），请手动结束后重试"
            )
        print("[bigqmt] MCP 服务已停止")
        return 0

    if action in ("start", "restart"):
        if action == "restart":
            runner.stop_external()
            killed, failed = _free_port(config.port)
            if killed:
                print(f"[bigqmt] 已清理仍占用端口 {config.port} 的遗留进程: {killed}")
            if failed:
                print(
                    f"[bigqmt] 警告：端口 {config.port} 仍被进程 {failed} 占用"
                    "（无法终止，可能权限不足），请手动结束后重试"
                )
        elif runner.is_running():
            # 守护进程语义：已在运行视为成功（不重复拉起）
            pid = _read_pid_file(runner.status()["pid_file"]) or runner.status()["pid"]
            print(
                f"[bigqmt] MCP 服务已在运行 pid={pid} "
                f"http://{config.host}:{config.port}/mcp"
            )
            if not args.skip_qmt_check:
                _warn_if_backend_down(config)
            return 0
        else:
            # 上次异常退出可能留下占用端口的子进程，先清理再拉起
            killed, failed = _free_port(config.port)
            if killed:
                print(f"[bigqmt] 已清理仍占用端口 {config.port} 的遗留进程: {killed}")
            if failed:
                print(
                    f"[bigqmt] 警告：端口 {config.port} 仍被进程 {failed} 占用"
                    "（无法终止，可能权限不足），请手动结束后重试"
                )

        if not args.skip_qmt_check:
            _warn_if_backend_down(config)

        if not runner.start(timeout=args.timeout):
            print(f"[bigqmt] MCP 服务启动失败: {runner.status()['last_error']}")
            return 1
        status = runner.status()
        print(
            f"[bigqmt] MCP 服务已启动 pid={status['pid']} "
            f"http://{config.host}:{config.port}/mcp"
        )
        print(f"[bigqmt] 日志: {status['log_file']}")
        return 0

    return 2


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
    if args.command == "mcp":
        return _cmd_mcp(args)

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
