r"""策略运行规格与运行器：StrategySpec 构建、命令行拼装、子进程监督。

对应 docs/QMT_STRATEGY_RUNNER_PLAN.md 第 4 节：
- S1 落地：QmtConfig 新增 strategy_* 字段解析 -> StrategySpec -> build_command；
- S2 将基于 StrategySpec 实现 StrategyRunner（子进程控制 + 监督 + 日志）。

命令拼装按 Windows CreateProcess / CommandLineToArgvW 语义切分，保证
`D:\strat\main.py` 这类含反斜杠的路径不被破坏；命令行首 token 若是裸的
`python`/`python.exe`，则替换为实际解释器（QMT_STRATEGY_PYTHON 或 sys.executable）。

S2 在此实现 StrategyRunner（拉起/启动确认/监督重启/停止/PID 与日志），
可注入 FakeSpawner 进行确定性单测。
"""

from __future__ import annotations

import enum
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from bigqmt.config import QmtConfig

# 命令首 token 为裸解释器名时，按此替换为实际 python（不处理带路径的显式解释器）
_PYTHON_TOKENS = {"python", "python.exe"}


class StrategyMode(str, enum.Enum):
    """策略运行模式。"""

    ONCE = "once"  # 一次性：策略退出即结束，不自动重启
    SUPERVISE = "supervise"  # 监督：崩溃后自动重启（有上限）


class StrategyConfigError(ValueError):
    """策略规格非法（如启用却未配置命令 / 模式非法 / 命令为空）。"""


class StrategyState(str, enum.Enum):
    """策略进程生命周期状态。"""

    IDLE = "idle"
    LAUNCHING = "launching"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


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


logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------- #
# StrategyRunner：子进程控制 + 监督 + 日志（S2）
# ---------------------------------------------------------------------- #
class _ChildProcess:
    """runner 依赖的子进程接口（鸭子类型：subprocess.Popen / 测试 Fake）。"""

    pid: Optional[int]

    def poll(self) -> Optional[int]:
        raise NotImplementedError

    def terminate(self) -> None:
        raise NotImplementedError

    def kill(self) -> None:
        raise NotImplementedError

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        raise NotImplementedError


def _default_spawn(
    argv: List[str],
    *,
    cwd: Optional[str] = None,
    log_file: Optional[os.PathLike] = None,
) -> _ChildProcess:
    """默认拉起实现：独立进程组 + stdout/stderr 追加写日志文件。"""
    log_handle = None
    kwargs: dict = {}
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(path, "ab")
        kwargs["stdout"] = log_handle
        kwargs["stderr"] = subprocess.STDOUT
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, **kwargs
        )
    finally:
        if log_handle is not None:
            log_handle.close()
    return process  # type: ignore[return-value]


def _default_pid_alive(pid: Optional[int]) -> bool:
    """默认 PID 存活探测（Windows 用 OpenProcess，POSIX 用 signal 0）。"""
    if not pid:
        return False
    try:
        if os.name == "nt":  # pragma: no cover - Windows 专属
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except Exception:  # pragma: no cover - 探测异常按不存在处理
        return False


class StrategyRunner:
    """策略子进程的运行器：拉起、确认、监督重启、停止、日志。

    语义：
    - mode=supervise：期望长驻进程；任何非预期的退出（含 exit 0）都视为崩溃，
      自动重启，超过 max_restarts 后转 ERROR；
    - mode=once：期望执行完即退出；进程退出即 STOPPED，不自动重启。

    可注入项（便于单测）：
    - spawner：`(argv, cwd=, log_file=) -> _ChildProcess`；
    - pid_alive：`(pid) -> bool`；watchdog=False 时关闭后台监督线程，由测试手动调用
      `check()` 驱动崩溃/重启迁移。
    """

    def __init__(
        self,
        spec: StrategySpec,
        *,
        spawner: Optional[Callable[..., _ChildProcess]] = None,
        pid_alive: Optional[Callable[[Optional[int]], bool]] = None,
        watchdog: bool = True,
        poll_interval: float = 1.0,
        sleep: Optional[Callable[[float], None]] = None,
        logger_obj: Optional[logging.Logger] = None,
        log_file: Optional[os.PathLike] = None,
        pid_file: Optional[os.PathLike] = None,
    ):
        if not spec.enabled:
            raise StrategyConfigError("策略命令为空（QMT_STRATEGY_CMD）")
        self.spec = spec
        self.mode = spec.mode
        self.max_restarts = spec.max_restarts
        self._spawner = spawner if spawner is not None else _default_spawn
        self._pid_alive = pid_alive if pid_alive is not None else _default_pid_alive
        self._watchdog_enabled = watchdog and self.mode == StrategyMode.SUPERVISE.value
        self._poll_interval = max(0.05, poll_interval)
        self._sleep = sleep if sleep is not None else time.sleep
        self._log = logger_obj if logger_obj is not None else logger

        base_dir = Path(spec.cwd) if spec.cwd else Path.cwd()
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_log = base_dir / "logs" / f"strategy_{run_id}.log"
        self.log_file = str(Path(log_file)) if log_file else str(spec.log_file or default_log)
        self.pid_file = str(Path(pid_file)) if pid_file else str(
            Path(self.log_file).with_suffix(".pid")
        )

        self._lock = threading.RLock()
        self._state = StrategyState.IDLE
        self._process: Optional[_ChildProcess] = None
        self._restart_count = 0
        self._last_error: Optional[str] = None
        self._last_exit_code: Optional[int] = None
        self._stop_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # 基础访问
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> StrategyState:
        with self._lock:
            return self._state

    @property
    def restart_count(self) -> int:
        with self._lock:
            return self._restart_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def status(self) -> dict:
        with self._lock:
            process = self._process
            running = process is not None and self._safe_poll(process) is None
            return {
                "state": self._state.value,
                "mode": self.mode,
                "running": running,
                "pid": process.pid if process is not None else None,
                "exit_code": self._last_exit_code,
                "restart_count": self._restart_count,
                "last_error": self._last_error,
                "log_file": self.log_file,
                "pid_file": self.pid_file,
                "command": self.spec.command,
            }

    @staticmethod
    def _safe_poll(process: _ChildProcess) -> Optional[int]:
        try:
            return process.poll()
        except Exception:  # pragma: no cover - 探测异常按存活处理
            return None

    # ------------------------------------------------------------------ #
    # PID 文件
    # ------------------------------------------------------------------ #
    def _write_pid(self, pid: int) -> None:
        try:
            Path(self.pid_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.pid_file).write_text(str(pid), encoding="utf-8")
        except OSError:  # pragma: no cover - 写失败不阻断运行
            self._log.warning("[StrategyRunner] 写入PID文件失败: %s", self.pid_file)

    def _remove_pid(self) -> None:
        try:
            Path(self.pid_file).unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    def _pid_file_live(self) -> bool:
        """已存在的 PID 文件且对应进程存活 -> True（防重复启动）。"""
        try:
            pid = int(Path(self.pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        return self._pid_alive(pid)

    # ------------------------------------------------------------------ #
    # 状态迁移
    # ------------------------------------------------------------------ #
    def _set_state(self, state: StrategyState) -> None:
        if state is self._state:
            return
        self._log.info("[StrategyRunner] %s -> %s", self._state.value, state.value)
        self._state = state

    def _fail(self, message: str) -> None:
        self._last_error = message
        self._log.error("[StrategyRunner] %s", message)
        self._set_state(StrategyState.ERROR)

    def _spawn(self) -> bool:
        """拉起子进程并写入 PID；返回是否成功。调用方须持有锁。"""
        argv = build_command(self.spec)
        try:
            process = self._spawner(
                argv, cwd=self.spec.cwd, log_file=self.log_file
            )
        except Exception as exc:
            self._process = None
            self._fail(f"拉起策略进程失败: {exc}")
            return False
        self._process = process
        self._write_pid(int(process.pid or 0))
        self._set_state(StrategyState.RUNNING)
        return True

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self, timeout: Optional[float] = None) -> bool:
        """启动策略进程（幂等），并等待启动确认窗口。

        确认窗口 = timeout 或 spec.start_timeout；窗口内进程仍存活视为启动成功，
        若提前退出则视为启动失败（不占用崩溃重启额度，直接置 ERROR）。
        """
        confirm = (
            float(timeout)
            if timeout is not None
            else float(self.spec.start_timeout or 0)
        )
        with self._lock:
            if self._state is StrategyState.RUNNING:
                return True
            if self._state is StrategyState.LAUNCHING:
                self._last_error = "策略正在启动中"
                return False
            if self._state is StrategyState.STOPPING:
                self._last_error = "策略正在停止中"
                return False
            if self._pid_file_live():
                self._last_error = "策略已在运行（PID文件检测）"
                return False

            self._restart_count = 0
            self._last_error = None
            self._last_exit_code = None
            self._remove_pid()
            self._set_state(StrategyState.LAUNCHING)
            if not self._spawn():
                return False

            # 启动确认（仅 supervise）：窗口内进程退出即视为启动失败
            if self.mode == StrategyMode.SUPERVISE.value and confirm > 0:
                deadline = time.monotonic() + confirm
                while True:
                    code = self._safe_poll(self._process)
                    if code is not None:
                        self._last_exit_code = code
                        self._process = None
                        self._remove_pid()
                        self._fail(f"策略启动后立即退出（exit={code}）")
                        return False
                    if time.monotonic() >= deadline:
                        break
                    self._sleep(min(0.2, max(0.01, confirm / 20)))

            if self._watchdog_enabled:
                self._ensure_watchdog()
            return True

    def stop(self, grace: Optional[float] = None) -> bool:
        """停止策略：先 terminate，超宽限期仍存活则 kill；不再自动重启。"""
        grace = grace if grace is not None else float(self.spec.grace_timeout or 10.0)
        with self._lock:
            if self._state in (StrategyState.IDLE, StrategyState.STOPPED):
                return True
            if self._state is StrategyState.STOPPING:
                return False
            self._set_state(StrategyState.STOPPING)

        self._stop_event.set()
        thread = self._watchdog_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

        with self._lock:
            process = self._process
            self._process = None
            if process is not None:
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=grace)
                    except Exception:  # 超时未退出 -> 强杀
                        self._log.warning("[StrategyRunner] 宽限超时，强制终止策略进程")
                        process.kill()
                        process.wait(timeout=grace)
                except Exception as exc:  # pragma: no cover - 防御性
                    self._log.warning("[StrategyRunner] 停止策略异常: %s", exc)
            self._remove_pid()
            self._restart_count = 0
            self._last_error = None
            self._set_state(StrategyState.STOPPED)
            return True

    def restart(self, timeout: Optional[float] = None) -> bool:
        """重启策略（stop 后重新 start，手动重启会重置重启额度）。"""
        if not self.stop():
            return False
        self._stop_event = threading.Event()
        return self.start(timeout=timeout)

    def is_running(self) -> bool:
        """当前进程是否在运行（本进程持有子进程时优先，否则查 PID 文件）。"""
        with self._lock:
            process = self._process
            if process is not None:
                return self._safe_poll(process) is None
        return self._pid_file_live()

    def stop_external(self) -> bool:
        """跨进程停止：按 PID 文件终止策略进程树（本进程未持有子进程时使用）。"""
        try:
            pid = int(Path(self.pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return True  # 无 PID 文件可视为已停止
        if not self._pid_alive(pid):
            self._remove_pid()
            return True
        self._log.warning("[StrategyRunner] 跨进程停止策略 PID=%s（整树）", pid)
        try:
            if os.name == "nt":  # pragma: no cover - Windows 专属
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                )
            else:  # pragma: no cover - QMT 仅 Windows，此处为兼容
                os.kill(pid, signal.SIGTERM)
        except OSError as exc:  # pragma: no cover
            self._log.warning("[StrategyRunner] 跨进程停止失败: %s", exc)
        self._remove_pid()
        return True

    def wait(self, timeout: Optional[float] = None) -> StrategyState:
        """阻塞至策略到达终态（once 模式进程退出/ERROR），适合前台跟随。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                if self._state in (StrategyState.STOPPED, StrategyState.ERROR):
                    return self._state
            self.check()
            if deadline is not None and time.monotonic() >= deadline:
                return self.state
            self._sleep(0.2)

    # ------------------------------------------------------------------ #
    # 监督（supervise 模式）
    # ------------------------------------------------------------------ #
    def _ensure_watchdog(self) -> None:
        with self._lock:
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._watchdog_loop,
                name="bigqmt-strategy-watchdog",
                daemon=True,
            )
            self._watchdog_thread = thread
            thread.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            try:
                self.check()
            except Exception:  # pragma: no cover - 心跳异常不应终止守护线程
                self._log.exception("[StrategyRunner] 心跳探测异常")

    def check(self) -> None:
        """单次检查（可被测试直接驱动）：进程退出 -> 崩溃/完成 -> 重启或 ERROR。"""
        with self._lock:
            if self._state is not StrategyState.RUNNING:
                return
            process = self._process
            if process is None:
                return
            code = self._safe_poll(process)
            if code is None:
                return

            # 进程已退出
            self._last_exit_code = code
            self._process = None
            self._remove_pid()

            if self.mode == StrategyMode.ONCE.value:
                # 一次性：正常完成
                self._log.info(
                    "[StrategyRunner] 策略完成退出（exit=%s）", code
                )
                self._set_state(StrategyState.STOPPED)
                return

            # supervise：视为崩溃，按上限自动重启
            self._restart_count += 1
            if self._restart_count > self.max_restarts:
                self._fail(
                    f"策略崩溃且超过重启上限({self.max_restarts})（exit={code}）"
                )
                return
            self._log.warning(
                "[StrategyRunner] 策略退出（exit=%s），第 %d/%d 次自动重启",
                code,
                self._restart_count,
                self.max_restarts,
            )
            if not self._spawn():
                return


class StrategySupervisor:
    """把 QMT 生命周期与策略运行器绑定（见 docs/QMT_STRATEGY_RUNNER_PLAN.md 第 3/4 节）。

    编排策略：
    - QMT 掉线（READY -> DISCONNECTED/ERROR/STOPPING）：先停策略，避免其持有失效通道；
    - QMT 恢复/首次就绪（* -> READY）：确保策略在运行（DISCONNECTED/ERROR 恢复时先停旧会话
      再以全新进程拉起）。

    manager 只需鸭子类型满足：add_state_listener / ensure_ready / stop / status；
    状态以字符串值比较（QmtState / StrategyState 均为 str 枚举），避免循环依赖。
    """

    _READY = "ready"
    _RUNNING_STATES = ("running", "launching", "stopping")
    _RECOVERY_FROM = ("disconnected", "error")

    def __init__(self, manager, runner: StrategyRunner):
        self._manager = manager
        self._runner = runner
        manager.add_state_listener(self._on_state)

    @property
    def runner(self) -> StrategyRunner:
        return self._runner

    def _value(self, state) -> str:
        return getattr(state, "value", state)

    def _runner_running(self) -> bool:
        return self._value(self._runner.state) in self._RUNNING_STATES

    def _on_state(self, old, new) -> None:
        new_v = self._value(new)
        old_v = self._value(old)
        if new_v == self._READY:
            if old_v in self._RECOVERY_FROM:
                # QMT 恢复：旧策略会话已不可信，先停再以全新进程拉起
                self._runner.stop()
            if not self._runner_running():
                # timeout=0：跳过阻塞式启动确认，交给运行器监督/前台跟随处理
                self._runner.start(timeout=0)
        elif old_v == self._READY and new_v in self._RECOVERY_FROM:
            self._runner.stop()

    def start(self, timeout: Optional[float] = None) -> bool:
        """确保 QMT READY 后拉起策略（幂等）。"""
        if not self._manager.ensure_ready(auto_start=True, timeout=timeout):
            return False
        if self._runner_running():
            return True
        return self._runner.start(timeout=0)

    def stop_strategy(self) -> bool:
        """仅停止策略与监督（保留 QMT 客户端与心跳）。"""
        return self._runner.stop()

    def stop(self, close_client: bool = False) -> bool:
        """停止策略并停止管理器（默认不关闭 QMT 客户端，避免误杀）。"""
        self._runner.stop()
        return self._manager.stop(close_client=close_client)

    def restart_strategy(self, timeout: Optional[float] = None) -> bool:
        """先停策略，再确保 QMT READY 并重启策略。"""
        if not self._manager.ensure_ready(auto_start=True, timeout=timeout):
            return False
        return self._runner.restart()

    def status(self) -> dict:
        status = dict(self._manager.status())
        status["strategy"] = self._runner.status()
        return status


__all__ = [
    "StrategyMode",
    "StrategyConfigError",
    "StrategyState",
    "StrategySpec",
    "StrategyRunner",
    "StrategySupervisor",
    "build_strategy_spec",
    "build_command",
]
