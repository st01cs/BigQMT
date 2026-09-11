"""QmtManager：QMT 生命周期状态机与统一管理入口（仅原生 QMT）。

对应 docs/QMT_STARTUP_PLAN.md 第 4/5 节：
- 显式状态机：STOPPED / STARTING / LOGIN / READY / DISCONNECTED / ERROR / STOPPING；
- READY = 进程存活 + 已登录（数据通道可用）；仅当 QMT_TRADING_REQUIRED=true
  时才额外要求交易通道就绪（P4）；
- 心跳线程定期探活，支持自动重启（有次数上限）；缓存就绪状态前必须复核，避免假阳性。

与真实客户端交互通过 QmtDriver 抽象进行：
- 默认驱动基于 P1 已落地的进程检测 + 可选 xtquant；
- P3 将接入 GUI 自动登录（pywinauto），P4 接入 XtQuantTrader 交易会话；
- 测试注入 FakeDriver，无需真实 QMT 即可验证状态迁移。
"""

from __future__ import annotations

import enum
import logging
import subprocess
import threading
import time
from functools import wraps
from typing import Callable, Optional

from bigqmt.config import QmtConfig, load_qmt_config
from ._auto_login import AutoLoginError, NativeQmtAutoLogin
from ._paths import QmtLocations, locate_qmt
from ._process import any_process_running

logger = logging.getLogger(__name__)


class QmtState(str, enum.Enum):
    """QMT 生命周期状态。"""

    STOPPED = "stopped"
    STARTING = "starting"
    LOGIN = "login"
    READY = "ready"
    DISCONNECTED = "disconnected"
    STOPPING = "stopping"
    ERROR = "error"


class QmtNotReadyError(RuntimeError):
    """QMT 未就绪时抛出（装饰器 / 上下文管理器使用）。"""


class QmtDriver:
    """Manager 依赖的底层能力接口。

    P3（自动登录）与 P4（交易会话）将通过真实实现逐步补齐，
    测试可使用 FakeDriver 注入。
    """

    def is_process_running(self) -> bool:
        raise NotImplementedError

    def is_logged_in(self) -> bool:
        raise NotImplementedError

    def trading_ready(self) -> bool:
        raise NotImplementedError

    def start_client(self) -> bool:
        raise NotImplementedError

    def login(self, timeout: Optional[int] = None) -> bool:
        raise NotImplementedError

    def close_client(self) -> bool:
        raise NotImplementedError


class DefaultQmtDriver(QmtDriver):
    """基于现有原语 + 可选 xtquant 的默认驱动。

    - 配置了密码时会构建 NativeQmtAutoLogin 执行 GUI 自动登录（P3）；
    - trading_ready() 在 P4（XtQuantTrader 会话）落地前保持 False。
    """

    def __init__(self, config: QmtConfig, locations: QmtLocations):
        self.config = config
        self.locations = locations
        names = list(config.process_names or ())
        if locations.exe_path is not None:
            base = locations.exe_path.name
            if base.lower() not in {n.lower() for n in names}:
                names.append(base)
        self._process_names = tuple(names) or ("XtItClient.exe",)
        self._auto_login = self._try_build_auto_login()
        self.last_error: Optional[str] = None

    def _try_build_auto_login(self) -> Optional[NativeQmtAutoLogin]:
        """构建自动登录器（需配置密码与可执行文件；缺一则返回 None）。"""
        if not self.config.password:
            return None
        exe = None
        if self.locations.exe_path is not None:
            exe = str(self.locations.exe_path)
        elif self.config.exe_path:
            exe = self.config.exe_path
        if not exe:
            return None
        data_dir = None
        if self.locations.userdata_dir is not None:
            data_dir = str(self.locations.userdata_dir)
        elif self.config.userdata_path:
            data_dir = self.config.userdata_path
        try:
            return NativeQmtAutoLogin(
                exe_path=exe,
                password=self.config.password,
                data_dir=data_dir,
                action_delay=self.config.login_action_delay,
                type_interval=self.config.login_type_interval,
            )
        except Exception:  # pragma: no cover - 构建失败则退化为无自动登录
            return None

    def is_process_running(self) -> bool:
        try:
            return any_process_running(self._process_names)
        except Exception as exc:  # pragma: no cover - 防御 tasklist 异常
            self.last_error = str(exc)
            return False

    def start_client(self) -> bool:
        if self.is_process_running():
            return True
        exe = self.locations.exe_path if self.locations else None
        if exe is None:
            self.last_error = "未定位到QMT可执行文件（请配置 QMT_EXE_PATH 或允许磁盘扫描）"
            return False
        try:
            subprocess.Popen([str(exe)])
            return True
        except Exception as exc:
            self.last_error = f"启动QMT客户端失败: {exc}"
            return False

    def _xtdata_connected(self) -> bool:
        try:
            from xtquant import xtdata

            return hasattr(xtdata, "is_connected") and bool(xtdata.is_connected())
        except Exception:
            return False

    def is_logged_in(self) -> bool:
        """登录态判定：xtquant 最可靠，其次自动登录器的窗口启发式。"""
        if self._xtdata_connected():
            return True
        if self._auto_login is not None:
            try:
                return bool(self._auto_login.is_logged_in())
            except Exception as exc:
                self.last_error = str(exc)
                return False
        return False

    def login(self, timeout: Optional[int] = None) -> bool:
        if self._auto_login is None:
            if not self.config.password:
                self.last_error = "未配置 QMT_PASSWORD，无法自动登录（可人工登录）"
            else:
                self.last_error = "未定位 QMT 可执行文件，无法自动登录"
            logger.warning("[DefaultQmtDriver] %s", self.last_error)
            return False
        try:
            ok = bool(
                self._auto_login.login(
                    timeout=timeout or int(self.config.login_timeout or 60)
                )
            )
            if not ok:
                self.last_error = self._auto_login.last_error or "自动登录未成功"
            return ok
        except AutoLoginError as exc:
            self.last_error = str(exc)
            return False
        except Exception as exc:  # pragma: no cover - 防御自动登录异常
            self.last_error = f"自动登录异常: {exc}"
            return False

    def trading_ready(self) -> bool:
        return False  # P4 接入 XtQuantTrader 会话

    def close_client(self) -> bool:
        logger.warning("默认驱动不自动关闭QMT客户端（避免误杀），请手动退出")
        return True


class QmtManager:
    """QMT 生命周期管理器（状态机 + 心跳 + 自动重启）。"""

    def __init__(
        self,
        config: Optional[QmtConfig] = None,
        driver: Optional[QmtDriver] = None,
        *,
        watchdog: bool = True,
        sleep: Optional[Callable[[float], None]] = None,
        logger_obj: Optional[logging.Logger] = None,
    ):
        self._config = config if config is not None else load_qmt_config()
        self._driver = driver if driver is not None else self._build_default_driver()
        self._watchdog_enabled = watchdog
        self._sleep = sleep if sleep is not None else time.sleep
        self._clock = time.monotonic
        self._log = logger_obj if logger_obj is not None else logger

        self._lock = threading.RLock()
        self._state = QmtState.STOPPED
        self._last_error: Optional[str] = None
        self._last_heartbeat: Optional[float] = None
        self._restart_count = 0
        self._stop_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._login_poll = 2.0
        self._last_login_attempt_at = float("-inf")
        self._state_listeners: list = []
        self._ready_listeners: list = []

    # ------------------------------------------------------------------ #
    # 基础访问
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> QmtState:
        with self._lock:
            return self._state

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def _build_default_driver(self) -> QmtDriver:
        locations = locate_qmt(self._config, allow_scan=True)
        return DefaultQmtDriver(self._config, locations)

    def status(self) -> dict:
        """返回当前状态快照（供 CLI / UI / 日志使用）。"""
        with self._lock:
            process = self._safe_probe(self._driver.is_process_running)
            logged = self._safe_probe(self._driver.is_logged_in)
            trading = self._safe_probe(self._driver.trading_ready)
            return {
                "state": self._state.value,
                "process_running": process,
                "logged_in": logged,
                "trading_ready": trading,
                "can_login": bool(self._config.password),
                "last_heartbeat": self._last_heartbeat,
                "last_error": self._last_error,
                "restart_count": self._restart_count,
                "config": {
                    "exe_path": self._config.exe_path,
                    "userdata_path": self._config.userdata_path,
                    "account_id": self._config.account_id,
                    "session_id": self._config.session_id,
                    "process_names": list(self._config.process_names),
                    "auto_start": self._config.auto_start,
                    "auto_restart": self._config.auto_restart,
                    "trading_required": self._config.trading_required,
                    "max_restarts": self._config.max_restarts,
                    "poll_interval": self._config.poll_interval,
                },
            }

    def add_state_listener(self, listener: Callable[[QmtState, QmtState], None]) -> None:
        """注册状态迁移监听器；仅在实际发生迁移（状态变化）时回调 listener(old, new)。"""
        with self._lock:
            self._state_listeners.append(listener)

    def add_ready_listener(self, callback: Callable[[], None]) -> None:
        """注册 on_ready 钩子：仅在迁移进入 READY（含 DISCONNECTED 恢复）时回调。"""

        def _wrapper(old: QmtState, new: QmtState) -> None:
            if new is QmtState.READY:
                callback()

        with self._lock:
            self._ready_listeners.append(_wrapper)
            self._state_listeners.append(_wrapper)

    @staticmethod
    def _safe_probe(probe: Callable[[], bool]) -> bool:
        try:
            return bool(probe())
        except Exception:  # pragma: no cover - 探针异常不应中断状态查询
            return False

    # ------------------------------------------------------------------ #
    # 状态迁移
    # ------------------------------------------------------------------ #
    def _set_state(self, state: QmtState) -> None:
        if state is self._state:
            return
        old = self._state
        self._log.info("[QmtManager] %s -> %s", self._state.value, state.value)
        self._state = state
        for listener in list(self._state_listeners):
            try:
                listener(old, state)
            except Exception:  # pragma: no cover - 监听器异常不应影响状态机
                self._log.exception("[QmtManager] 状态监听器异常")

    def _mark_ready(self) -> None:
        self._last_error = None
        self._restart_count = 0
        self._last_heartbeat = self._clock()
        self._set_state(QmtState.READY)

    def _fail(self, message: str) -> None:
        self._last_error = message
        self._log.error("[QmtManager] %s", message)
        self._set_state(QmtState.ERROR)

    def _is_ready(self) -> bool:
        """READY 判定：进程 + 登录为必需；交易通道仅在 trading_required 时要求。

        默认（数据/登录模式）下，登录成功即表示行情数据通道可用
        （xtdata.is_connected），因此无需等待 XtQuantTrader（P4）。
        """
        if not (
            self._safe_probe(self._driver.is_process_running)
            and self._safe_probe(self._driver.is_logged_in)
        ):
            return False
        if self._config.trading_required:
            return self._safe_probe(self._driver.trading_ready)
        return True

    def _attempt_login(self, timeout: int) -> bool:
        if not self._config.password:
            self._last_error = "等待人工登录（未配置 QMT_PASSWORD）"
            return False
        self._last_login_attempt_at = self._clock()
        try:
            ok = bool(self._driver.login(timeout=timeout))
            if not ok:
                self._last_error = "自动登录未成功（可能需要人工处理验证码）"
            return ok
        except Exception as exc:  # pragma: no cover - 防御驱动异常
            self._last_error = f"自动登录异常: {exc}"
            return False

    # ------------------------------------------------------------------ #
    # 生命周期操作
    # ------------------------------------------------------------------ #
    def start(self, timeout: Optional[float] = None) -> bool:
        """阻塞启动直至 READY（进程 + 登录 + 交易通道）。"""
        timeout = timeout if timeout is not None else float(self._config.login_timeout or 60)
        deadline = self._clock() + timeout
        with self._lock:
            if self._state is QmtState.READY:
                return True
            if self._state in (QmtState.STARTING, QmtState.LOGIN, QmtState.STOPPING):
                self._last_error = f"正在{self._state.value}，请稍候"
                return False

            self._set_state(QmtState.STARTING)
            if not self._safe_probe(self._driver.is_process_running):
                self._log.info("[QmtManager] 启动QMT客户端...")
                if not self._driver.start_client():
                    self._fail(getattr(self._driver, "last_error", None) or "启动QMT客户端失败")
                    return False

            while not self._safe_probe(self._driver.is_logged_in):
                self._set_state(QmtState.LOGIN)
                if self._clock() >= deadline:
                    self._fail(self._last_error or "登录超时")
                    return False
                if self._config.password:
                    if self._clock() - self._last_login_attempt_at >= self._login_poll:
                        remaining = max(1, int(deadline - self._clock()))
                        self._attempt_login(remaining)
                    else:
                        self._last_error = "自动登录进行中，等待结果..."
                else:
                    self._last_error = "等待人工登录（未配置 QMT_PASSWORD）"
                self._sleep(self._login_poll)

            if (
                self._config.trading_required
                and not self._safe_probe(self._driver.trading_ready)
            ):
                self._last_error = (
                    "交易通道未就绪（QMT_TRADING_REQUIRED=true，需 P4 接入 XtQuantTrader）"
                )
                self._set_state(QmtState.LOGIN)
                return False

            self._mark_ready()
            if self._watchdog_enabled:
                self._ensure_watchdog()
            return True

    def login(self, timeout: Optional[float] = None) -> bool:
        """仅执行登录流程（客户端须已运行）。"""
        timeout = timeout if timeout is not None else float(self._config.login_timeout or 60)
        with self._lock:
            if not self._safe_probe(self._driver.is_process_running):
                self._last_error = "QMT客户端未运行，请先 start"
                return False
            if self._safe_probe(self._driver.is_logged_in):
                return True
            self._set_state(QmtState.LOGIN)
            ok = self._attempt_login(int(timeout))
            if ok and self._safe_probe(self._driver.is_logged_in):
                self._last_error = None
                self._set_state(QmtState.LOGIN)  # 交易通道就绪前不置 READY
            return ok

    def stop(self, close_client: bool = False) -> bool:
        """停止管理器（停止心跳；默认不关闭 QMT 客户端，避免误杀）。"""
        with self._lock:
            if self._state is QmtState.STOPPED:
                return True
            if self._state is QmtState.STOPPING:
                return False
            self._set_state(QmtState.STOPPING)

        self._stop_event.set()
        thread = self._watchdog_thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=5)

        if close_client:
            try:
                self._driver.close_client()
            except Exception as exc:  # pragma: no cover
                self._log.warning("[QmtManager] 关闭客户端异常: %s", exc)

        with self._lock:
            self._restart_count = 0
            self._last_error = None
            self._set_state(QmtState.STOPPED)
        return True

    def restart(self, timeout: Optional[float] = None, close_client: bool = True) -> bool:
        """重启：关闭（可选）后重新启动至 READY。"""
        if not self.stop(close_client=close_client):
            return False
        self._stop_event = threading.Event()
        return self.start(timeout=timeout)

    def ensure_ready(
        self,
        auto_start: bool = True,
        timeout: Optional[float] = None,
    ) -> bool:
        """确保 QMT 就绪；未就绪时按需自动启动。"""
        with self._lock:
            if self._state is QmtState.READY and self._is_ready():
                return True
            if self._state is QmtState.ERROR:
                self._log.info("[QmtManager] 从 ERROR 重置为 STOPPED")
                self._state = QmtState.STOPPED
            if auto_start:
                return self.start(timeout=timeout)
            return False

    # ------------------------------------------------------------------ #
    # 心跳与自动重启
    # ------------------------------------------------------------------ #
    def _ensure_watchdog(self) -> None:
        with self._lock:
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._watchdog_loop,
                name="bigqmt-watchdog",
                daemon=True,
            )
            self._watchdog_thread = thread
            thread.start()

    def _watchdog_loop(self) -> None:
        interval = max(0.5, float(self._config.poll_interval or 20.0))
        while not self._stop_event.wait(interval):
            try:
                self._heartbeat_once()
            except Exception:  # pragma: no cover - 心跳异常不应终止守护线程
                self._log.exception("[QmtManager] 心跳探测异常")

    def _heartbeat_once(self) -> None:
        """单次心跳探测（可被单元测试直接调用）。"""
        with self._lock:
            if self._state not in (
                QmtState.READY,
                QmtState.DISCONNECTED,
                QmtState.LOGIN,
                QmtState.STARTING,
            ):
                return
            if self._is_ready():
                self._mark_ready()
                return

            self._last_error = "QMT状态异常：进程或连接已断开"
            if self._state is not QmtState.DISCONNECTED:
                self._set_state(QmtState.DISCONNECTED)

            if not self._config.auto_restart:
                self._fail("QMT状态异常且自动重启未启用")
                return
            if self._restart_count >= self._config.max_restarts:
                self._fail(f"自动重启失败：超过上限({self._config.max_restarts})")
                return

            self._restart_count += 1
            self._log.warning(
                "[QmtManager] 尝试自动重启（第 %d/%d 次）",
                self._restart_count,
                self._config.max_restarts,
            )
            if not self._safe_probe(self._driver.is_process_running):
                self._driver.start_client()
            if self._config.password:
                self._attempt_login(int(self._config.login_timeout or 60))
            # 状态保持 DISCONNECTED，由下一次心跳确认是否恢复

    # ------------------------------------------------------------------ #
    # 上下文管理
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "QmtManager":
        if not self.ensure_ready(auto_start=True):
            raise QmtNotReadyError(self.last_error or "QMT就绪失败")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.stop(close_client=False)
        return False


# ---------------------------------------------------------------------- #
# 模块级单例与便捷函数
# ---------------------------------------------------------------------- #
_manager: Optional[QmtManager] = None
_manager_lock = threading.RLock()


def get_qmt_manager(
    config: Optional[QmtConfig] = None,
    driver: Optional[QmtDriver] = None,
) -> QmtManager:
    """获取全局 QmtManager 单例（首次调用时构建）。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = QmtManager(config=config, driver=driver)
        return _manager


def get_qmt_status() -> dict:
    """便捷：QMT 状态快照。"""
    return get_qmt_manager().status()


def ensure_ready(auto_start: bool = True, timeout: Optional[float] = None) -> bool:
    """便捷：确保 QMT 就绪。"""
    return get_qmt_manager().ensure_ready(auto_start=auto_start, timeout=timeout)


def require_ready(auto_start: bool = True, timeout: Optional[float] = None):
    """装饰器：确保 QMT 就绪后再执行被装饰函数。"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            manager = get_qmt_manager()
            if not manager.ensure_ready(auto_start=auto_start, timeout=timeout):
                raise QmtNotReadyError(manager.last_error or "QMT未就绪")
            return func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "QmtState",
    "QmtNotReadyError",
    "QmtDriver",
    "DefaultQmtDriver",
    "QmtManager",
    "get_qmt_manager",
    "get_qmt_status",
    "ensure_ready",
    "require_ready",
]
