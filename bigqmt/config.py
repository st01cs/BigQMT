"""BigQMT 配置：读取 .env / 环境变量（仅原生 QMT）。

设计说明（对应 docs/QMT_STARTUP_PLAN.md 第 5 节）：
- 显式配置优先，缺失项由后续路径/进程自动发现兜底；
- 解析函数保持纯净（`config_from_mapping`），便于单元测试；
- python-dotenv 为可选依赖，缺失时仅回退到进程环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

try:  # python-dotenv 为可选依赖
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    _load_dotenv = None


class QmtConfigError(ValueError):
    """QMT 配置解析/校验错误。"""


_TRUE_VALUES = {"1", "true", "yes", "on", "y"}


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _to_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_csv_tuple(value: Any, default: Tuple[str, ...]) -> Tuple[str, ...]:
    if value is None:
        return default
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    return tuple(parts) if parts else default


@dataclass(frozen=True)
class QmtConfig:
    """原生 QMT 运行配置（不可变快照）。"""

    exe_path: Optional[str] = None
    userdata_path: Optional[str] = None
    account_id: Optional[str] = None
    password: Optional[str] = None
    session_id: Optional[int] = None
    process_names: Tuple[str, ...] = ("XtItClient.exe",)
    auto_start: bool = False
    auto_restart: bool = True
    trading_required: bool = False
    max_restarts: int = 3
    poll_interval: float = 20.0
    login_timeout: int = 60
    manual_login_wait: int = 120
    # 自动登录动作节奏（GUI 焦点/输入框就绪需要时间，避免密码填错框）
    login_action_delay: float = 0.5
    login_type_interval: float = 0.05
    # ---- 策略自动运行（S1，见 docs/QMT_STRATEGY_RUNNER_PLAN.md 第 4 节） ----
    strategy_enabled: bool = False
    strategy_cmd: Optional[str] = None
    strategy_python: Optional[str] = None
    strategy_cwd: Optional[str] = None
    strategy_mode: str = "supervise"
    strategy_max_restarts: int = 3
    strategy_log: Optional[str] = None
    strategy_start_timeout: int = 30
    strategy_grace: float = 10.0

    def with_updates(self, **kwargs: Any) -> "QmtConfig":
        """返回替换指定字段后的新配置（frozen dataclass 不可原地修改）。"""
        return QmtConfig(**{**self.__dict__, **kwargs})


def config_from_mapping(mapping: Mapping[str, Any]) -> QmtConfig:
    """从键值映射（环境变量/.env）解析 QmtConfig。

    userdata 目录支持两个键：优先 QMT_USERDATA_PATH，回退 QMT_DATA_DIR。
    """
    get = mapping.get
    userdata = _to_str(get("QMT_USERDATA_PATH"))
    if not userdata:
        userdata = _to_str(get("QMT_DATA_DIR"))
    session_raw = _to_str(get("QMT_SESSION_ID"))
    session_id = None
    if session_raw:
        try:
            session_id = int(session_raw)
        except (TypeError, ValueError):
            session_id = None
    return QmtConfig(
        exe_path=_to_str(get("QMT_EXE_PATH")) or None,
        userdata_path=userdata or None,
        account_id=_to_str(get("QMT_ACCOUNT_ID")) or None,
        password=_to_str(get("QMT_PASSWORD")) or None,
        session_id=session_id,
        process_names=_to_csv_tuple(get("QMT_PROCESS_NAMES"), ("XtItClient.exe",)),
        auto_start=_to_bool(get("QMT_AUTO_START"), False),
        auto_restart=_to_bool(get("QMT_AUTO_RESTART"), True),
        trading_required=_to_bool(get("QMT_TRADING_REQUIRED"), False),
        max_restarts=_to_int(get("QMT_MAX_RESTARTS"), 3),
        poll_interval=_to_float(get("QMT_POLL_INTERVAL"), 20.0),
        login_timeout=_to_int(get("QMT_LOGIN_TIMEOUT"), 60),
        manual_login_wait=_to_int(get("QMT_MANUAL_LOGIN_WAIT"), 120),
        login_action_delay=_to_float(get("QMT_LOGIN_ACTION_DELAY"), 0.5),
        login_type_interval=_to_float(get("QMT_LOGIN_TYPE_INTERVAL"), 0.05),
        strategy_enabled=_to_bool(get("QMT_STRATEGY_ENABLED"), False),
        strategy_cmd=_to_str(get("QMT_STRATEGY_CMD")) or None,
        strategy_python=_to_str(get("QMT_STRATEGY_PYTHON")) or None,
        strategy_cwd=_to_str(get("QMT_STRATEGY_CWD")) or None,
        strategy_mode=_to_str(get("QMT_STRATEGY_MODE"), "supervise"),
        strategy_max_restarts=_to_int(get("QMT_STRATEGY_MAX_RESTARTS"), 3),
        strategy_log=_to_str(get("QMT_STRATEGY_LOG")) or None,
        strategy_start_timeout=_to_int(get("QMT_STRATEGY_START_TIMEOUT"), 30),
        strategy_grace=_to_float(get("QMT_STRATEGY_GRACE"), 10.0),
    )


def _find_env_file(start: Optional[Path] = None) -> Optional[Path]:
    """从 start（默认 cwd）向上逐级寻找 .env。"""
    base = Path(start or Path.cwd()).resolve()
    for directory in [base, *base.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(path: os.PathLike) -> dict:
    """标准库 .env 解析（python-dotenv 缺失时的兜底）。

    规则：KEY=VALUE，跳过空行与 # 注释，去除首尾引号；
    不做变量展开，保持与 python-dotenv 的基础行为一致。
    """
    result = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_qmt_config(env_file: Optional[os.PathLike] = None) -> QmtConfig:
    """加载 .env（可选）后，从进程环境变量解析 QmtConfig。"""
    target = Path(env_file) if env_file is not None else _find_env_file()
    if target is not None and Path(target).is_file():
        if _load_dotenv is not None:
            _load_dotenv(Path(target))
        else:
            # python-dotenv 缺失时使用标准库兜底，且不覆盖已有环境变量
            for key, value in parse_env_file(target).items():
                if key not in os.environ:
                    os.environ[key] = value
    return config_from_mapping(os.environ)


__all__ = [
    "QmtConfig",
    "QmtConfigError",
    "config_from_mapping",
    "load_qmt_config",
    "parse_env_file",
]
