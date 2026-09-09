"""原生 QMT 进程检测（Windows；tasklist）。

与 _connection.py 的 READY 判定不同，本模块只回答「进程是否在运行」，
不含登录态/通道连通性判断（后者依赖 xtquant/pywinauto，归入后续里程碑）。
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from typing import Callable, Dict, Iterable, Optional

TasklistReader = Callable[[], str]


def parse_tasklist_csv(text: str) -> Dict[str, int]:
    """解析 `tasklist /FO CSV /NH` 输出 → {映像名(小写): 第一个 pid}。"""
    result: Dict[str, int] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        name = row[0].strip().lower()
        pid = row[1].strip()
        if name and pid.isdigit():
            result.setdefault(name, int(pid))
    return result


def _run_tasklist() -> str:
    """运行系统 tasklist（仅 Windows；非 Windows 返回空）。"""
    if sys.platform != "win32":
        return ""
    proc = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.stdout or ""


def running_processes(
    reader: Optional[TasklistReader] = None,
) -> Dict[str, int]:
    """返回当前进程快照 {映像名(小写): pid}。reader 可注入以便测试。"""
    text = (reader() if reader is not None else _run_tasklist()) or ""
    return parse_tasklist_csv(text)


def find_process_pid(
    image_name: str,
    reader: Optional[TasklistReader] = None,
) -> Optional[int]:
    """查找指定进程映像名对应的 pid；未运行返回 None。"""
    return running_processes(reader=reader).get(image_name.strip().lower())


def any_process_running(
    image_names: Iterable[str],
    reader: Optional[TasklistReader] = None,
) -> bool:
    """任一指定进程在运行即返回 True。"""
    return any(
        find_process_pid(name, reader=reader) is not None
        for name in image_names
    )


__all__ = [
    "parse_tasklist_csv",
    "running_processes",
    "find_process_pid",
    "any_process_running",
]
