"""把 `bigqmt.mcp` 服务作为受管子进程运行。

复用 `bigqmt.core.qmt._strategy` 的 `StrategyRunner`（PID 文件、启动确认、
崩溃重启、停止），避免为 MCP 服务再造一套进程管理。

设计约定：
- 敏感项不落命令行：QMT token / MCP Bearer token 通过环境变量（含 `.env`）继承，
  只把 host/port 等非敏感项写进 argv；
- 子进程 cwd 固定为仓库根目录，`python -m bigqmt.mcp` 才能在未安装包时也可用。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from bigqmt.core.qmt._strategy import StrategyRunner, StrategySpec
from bigqmt.mcp.config import McpConfig, load_mcp_config

#: 仓库根目录（bigqmt/mcp/runner.py → 上三级）
REPO_ROOT = Path(__file__).resolve().parents[2]


def default_log_file(repo_root: Optional[Path] = None) -> str:
    return str(Path(repo_root or REPO_ROOT) / "logs" / "mcp_server.log")


def default_pid_file(repo_root: Optional[Path] = None) -> str:
    return str(Path(repo_root or REPO_ROOT) / "logs" / "mcp_server.pid")


def build_mcp_command(config: McpConfig) -> str:
    """构造 MCP 服务命令行（首 token 用裸 `python`，由 build_command 替换为真实解释器）。"""
    parts = [
        "python",
        "-m",
        "bigqmt.mcp",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--qmt-url",
        config.qmt_base_url,
    ]
    if config.allow_remote:
        parts.append("--allow-remote")
    return " ".join(parts)


def build_mcp_spec(
    config: McpConfig,
    *,
    python: Optional[str] = None,
    cwd: Optional[str] = None,
    log_file: Optional[str] = None,
    mode: str = "supervise",
    max_restarts: int = 3,
    start_timeout: float = 15.0,
    grace_timeout: float = 10.0,
) -> StrategySpec:
    """由 McpConfig 构造 MCP 服务的 StrategySpec。"""
    return StrategySpec(
        command=build_mcp_command(config),
        python=python or sys.executable,
        cwd=str(cwd or REPO_ROOT),
        mode=mode,
        max_restarts=max_restarts,
        start_timeout=start_timeout,
        grace_timeout=grace_timeout,
        log_file=log_file or default_log_file(),
    )


def build_runner(
    config: Optional[McpConfig] = None,
    *,
    python: Optional[str] = None,
    cwd: Optional[str] = None,
    log_file: Optional[str] = None,
    pid_file: Optional[str] = None,
    spawner=None,
    pid_alive=None,
    watchdog: bool = True,
    poll_interval: float = 1.0,
    sleep=None,
    logger_obj=None,
) -> StrategyRunner:
    """构造 MCP 服务运行器（可注入 spawner/pid_alive 便于测试）。"""
    spec = build_mcp_spec(
        config or load_mcp_config(), python=python, cwd=cwd, log_file=log_file
    )
    return StrategyRunner(
        spec,
        spawner=spawner,
        pid_alive=pid_alive,
        watchdog=watchdog,
        poll_interval=poll_interval,
        sleep=sleep,
        logger_obj=logger_obj,
        log_file=spec.log_file,
        pid_file=pid_file or default_pid_file(cwd),
    )


__all__ = [
    "REPO_ROOT",
    "build_mcp_command",
    "build_mcp_spec",
    "build_runner",
    "default_log_file",
    "default_pid_file",
]
