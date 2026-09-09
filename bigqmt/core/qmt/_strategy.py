"""策略运行规格（S1）：StrategySpec 构建与命令行拼装。

对应 docs/QMT_STRATEGY_RUNNER_PLAN.md 第 4 节：
- S1 落地：QmtConfig 新增 strategy_* 字段解析 -> StrategySpec -> build_command；
- S2 将基于 StrategySpec 实现 StrategyRunner（子进程控制 + 监督 + 日志）。

命令拼装按 Windows CreateProcess / CommandLineToArgvW 语义切分，保证
`D:\strat\main.py` 这类含反斜杠的路径不被破坏；命令行首 token 若是裸的
`python`/`python.exe`，则替换为实际解释器（QMT_STRATEGY_PYTHON 或 sys.executable）。
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass
from typing import List, Optional

from bigqmt.config import QmtConfig

# 命令首 token 为裸解释器名时，按此替换为实际 python（不处理带路径的显式解释器）
_PYTHON_TOKENS = {"python", "python.exe"}


class StrategyMode(str, enum.Enum):
    """策略运行模式。"""

    ONCE = "once"  # 一次性：策略退出即结束，不自动重启
    SUPERVISE = "supervise"  # 监督：崩溃后自动重启（有上限）


class StrategyConfigError(ValueError):
    """策略规格非法（如启用却未配置命令 / 模式非法 / 命令为空）。"""


@dataclass(frozen=True)
class StrategySpec:
    """一条待运行策略的规格（与 QmtConfig 解耦的纯数据）。

    - command：原始命令行字符串，如 `python D:\\strat\\main.py --trade`；
    - python：指定解释器；为 None 且 command 首 token 是裸 `python` 时，
      拼装阶段回退到 `sys.executable`；
    - mode：once | supervise；
    - max_restarts：supervise 模式下崩溃自动重启上限；
    - start_timeout / grace_timeout：启动确认超时 / 停止宽限（秒）；
    - log_file：日志路径（None = 使用默认 `logs/strategy_<ts>.log`）。
    """

    command: Optional[str] = None
    python: Optional[str] = None
    cwd: Optional[str] = None
    mode: str = StrategyMode.SUPERVISE.value
    max_restarts: int = 3
    start_timeout: int = 30
    grace_timeout: float = 10.0
    log_file: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """是否有可执行命令。"""
        return bool(self.command and self.command.strip())


def _split_windows_commandline(line: str) -> List[str]:
    """按 CommandLineToArgvW 语义切分命令行（保留反斜杠、正确处理引号）。"""
    args: List[str] = []
    current: List[str] = []
    in_quotes = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\":
            j = i
            while j < n and line[j] == "\\":
                j += 1
            count = j - i
            if j < n and line[j] == '"':
                # 引号前：成对反斜杠 -> 字面反斜杠；奇数个 -> 末个转义引号
                current.append("\\" * (count // 2))
                if count % 2 == 1:
                    current.append('"')
                    i = j + 1
                    continue
                in_quotes = not in_quotes
                i = j + 1
                continue
            current.append("\\" * count)
            i = j
            continue
        if ch == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if ch in (" ", "\t") and not in_quotes:
            if current:
                args.append("".join(current))
                current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        args.append("".join(current))
    return args


def build_strategy_spec(config: QmtConfig) -> StrategySpec:
    """从 QmtConfig 构建 StrategySpec，并校验关键配置。

    仅当 strategy_enabled=true 时才要求必须配置命令；模式非法时始终报错。
    """
    mode = (config.strategy_mode or "").strip().lower()
    if mode not in (StrategyMode.ONCE.value, StrategyMode.SUPERVISE.value):
        raise StrategyConfigError(
            f"非法的 QMT_STRATEGY_MODE: {config.strategy_mode!r}（可选 once|supervise）"
        )
    spec = StrategySpec(
        command=config.strategy_cmd,
        python=config.strategy_python,
        cwd=config.strategy_cwd,
        mode=mode,
        max_restarts=config.strategy_max_restarts,
        start_timeout=config.strategy_start_timeout,
        grace_timeout=config.strategy_grace,
        log_file=config.strategy_log,
    )
    if config.strategy_enabled and not spec.enabled:
        raise StrategyConfigError(
            "QMT_STRATEGY_ENABLED=true 但未配置 QMT_STRATEGY_CMD"
        )
    return spec


def build_command(spec: StrategySpec) -> List[str]:
    """把策略命令行切分为 argv；裸 `python` 解释器替换为实际路径。"""
    if not spec.enabled:
        raise StrategyConfigError("策略命令为空（QMT_STRATEGY_CMD）")
    argv = _split_windows_commandline(spec.command.strip())
    if not argv:
        raise StrategyConfigError("策略命令为空（QMT_STRATEGY_CMD）")
    first = argv[0]
    if (
        "/" not in first
        and "\\" not in first
        and first.lower() in _PYTHON_TOKENS
    ):
        argv = [spec.python or sys.executable, *argv[1:]]
    return argv


__all__ = [
    "StrategyMode",
    "StrategyConfigError",
    "StrategySpec",
    "build_strategy_spec",
    "build_command",
]
