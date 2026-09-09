"""BigQMT QMT 核心：QMT 启动与管理（仅原生 QMT）。

P0/P1 已落地：配置解析（bigqmt.config）、安装路径发现（_paths）、
进程检测（_process）。后续里程碑将在此扩展 _connection / _auto_login / _trader。
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
]
