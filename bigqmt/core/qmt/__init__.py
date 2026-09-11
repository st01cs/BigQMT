"""BigQMT QMT 核心：QMT 启动与管理（仅原生 QMT）。

已落地里程碑：
- P0/P1：配置解析（bigqmt.config）、安装路径发现（_paths）、进程检测（_process）；
- P2：QmtManager 状态机 + 心跳/自动重启 + 装饰器/上下文/CLI（_connection / cli）；
- P3：原生客户端 GUI 自动登录（_auto_login，pywinauto），已接入 DefaultQmtDriver；
- S1~S4：策略自动运行与监督（_strategy：StrategySpec / StrategyRunner / StrategySupervisor），
  真机机制 E2E 已验收；
默认数据/登录模式：READY = 进程存活 + 已登录（xtdata 连通），无需交易会话；
P4 待接入（可选）：XtQuantTrader 交易会话（_trader），由 QMT_TRADING_REQUIRED 开关启用。
"""

from bigqmt.config import (
    QmtConfig,
    QmtConfigError,
    config_from_mapping,
    load_qmt_config,
)
from ._paths import (
    QmtLocations,
    QMT_EXE_NAMES,
    QMT_USERDATA_SUBPATHS,
    discover_exe,
    find_userdata_subdir,
    locate_qmt,
    looks_like_qmt_install,
    scan_qmt_install_paths,
)
from ._process import (
    any_process_running,
    find_process_pid,
    parse_tasklist_csv,
    running_processes,
)
from ._connection import (
    QmtState,
    QmtNotReadyError,
    QmtManager,
    get_qmt_manager,
    get_qmt_status,
    ensure_ready,
    require_ready,
)

__all__ = [
    # config
    "QmtConfig",
    "QmtConfigError",
    "config_from_mapping",
    "load_qmt_config",
    # paths
    "QmtLocations",
    "QMT_EXE_NAMES",
    "QMT_USERDATA_SUBPATHS",
    "discover_exe",
    "find_userdata_subdir",
    "locate_qmt",
    "looks_like_qmt_install",
    "scan_qmt_install_paths",
    # process
    "any_process_running",
    "find_process_pid",
    "parse_tasklist_csv",
    "running_processes",
    # connection / lifecycle
    "QmtState",
    "QmtNotReadyError",
    "QmtManager",
    "get_qmt_manager",
    "get_qmt_status",
    "ensure_ready",
    "require_ready",
]
