"""原生 QMT 安装路径发现。

仅面向原生 QMT（完整版客户端）：
- 以安装目录下存在 `userdata_mini` / `userdata` 子目录为识别特征；
- 已知可执行文件名仅作辅助发现，最终以配置（QMT_EXE_PATH）为准；
- 磁盘扫描限定深度并按关键词剪枝，控制耗时。
"""

from __future__ import annotations

import os
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from bigqmt.config import QmtConfig

# 原生 QMT 数据目录识别特征（xtquant 连接通常使用 userdata_mini）
QMT_USERDATA_SUBPATHS: Tuple[str, ...] = ("userdata_mini", "userdata")

# 原生 QMT 可执行文件名（不同券商可能存在差异，仅用于辅助发现）
QMT_EXE_NAMES: Tuple[str, ...] = ("XtItClient.exe",)

# 相对安装根的可执行文件目录
QMT_EXE_DIRS: Tuple[str, ...] = ("bin.x64", "bin", "bin64")

# 磁盘扫描第二层允许进入的目录名关键词（避免全盘遍历）
_RECURSE_KEYWORDS: Tuple[str, ...] = (
    "qmt",
    "xtquant",
    "迅投",
    "交易",
    "证券",
    "program files",
    "软件",
)


@dataclass(frozen=True)
class QmtLocations:
    """QMT 关键路径定位结果。"""

    install_dir: Optional[Path] = None
    userdata_dir: Optional[Path] = None
    exe_path: Optional[Path] = None


def iter_drive_roots() -> Iterable[Path]:
    """枚举本机存在的盘符根目录（QMT 仅支持 Windows）。"""
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            if root.exists():
                yield root


def find_userdata_subdir(dir_path: Path) -> Optional[Path]:
    """返回 dir_path 下第一个已存在的 QMT 数据子目录（userdata_mini 优先）。"""
    for name in QMT_USERDATA_SUBPATHS:
        candidate = Path(dir_path) / name
        if candidate.is_dir():
            return candidate
    return None


def looks_like_qmt_install(dir_path: Path) -> bool:
    """目录是否疑似 QMT 安装根（含 userdata_mini / userdata）。"""
    return find_userdata_subdir(dir_path) is not None


def scan_qmt_install_paths(
    roots: Optional[Iterable[Path]] = None,
    max_depth: int = 2,
) -> List[Path]:
    """扫描磁盘，返回疑似 QMT 安装根目录列表（含 userdata 子目录）。

    层 1：根目录的直接子目录全量检查；
    层 2：仅进入名称含 QMT 相关关键词的目录，避免全盘遍历。
    若传入 roots，则只扫描这些根目录（便于测试与收敛范围）。
    """
    root_list = list(roots) if roots is not None else list(iter_drive_roots())
    found: List[Path] = []
    seen = set()

    def _check(dir_path: Path) -> None:
        try:
            key = str(Path(dir_path).resolve()).lower()
        except OSError:
            key = str(dir_path).lower()
        if key in seen:
            return
        seen.add(key)
        if find_userdata_subdir(dir_path) is not None:
            found.append(Path(dir_path))

    for root in root_list:
        root = Path(root)
        if not root.is_dir():
            continue
        _check(root)
        if max_depth < 1:
            continue
        try:
            level1 = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d1 in level1:
            _check(d1)
            if max_depth < 2:
                continue
            if not any(kw in d1.name.lower() for kw in _RECURSE_KEYWORDS):
                continue
            try:
                for d2 in d1.iterdir():
                    if d2.is_dir():
                        _check(d2)
            except OSError:
                continue

    return sorted(set(found), key=lambda p: str(p).lower())


def discover_exe(
    install_dir: Path,
    exe_names: Iterable[str] = QMT_EXE_NAMES,
) -> Optional[Path]:
    """在安装目录常见子目录中查找原生 QMT 可执行文件。"""
    names = list(exe_names) or list(QMT_EXE_NAMES)
    candidates: List[Path] = []
    for rel in QMT_EXE_DIRS:
        for name in names:
            candidates.append(Path(install_dir) / rel / name)
    for name in names:
        candidates.append(Path(install_dir) / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def locate_qmt(
    config: QmtConfig,
    allow_scan: bool = True,
) -> QmtLocations:
    """根据配置（+ 磁盘自动发现兜底）定位 QMT 关键路径。

    优先级：
    1. 配置给出的 userdata 目录 / exe 路径优先采用；
    2. 缺失部分用磁盘扫描结果补齐（安装根 → userdata / exe）。

    allow_scan=False 时不做全盘扫描（测试或受限环境使用），
    此时缺失的目录/可执行文件将保持 None。
    """
    userdata_dir: Optional[Path] = None
    if config.userdata_path:
        candidate = Path(config.userdata_path)
        if candidate.is_dir():
            userdata_dir = candidate

    install_dir: Optional[Path] = None
    if userdata_dir is not None:
        install_dir = userdata_dir.parent
    elif allow_scan:
        installs = scan_qmt_install_paths()
        if installs:
            install_dir = installs[0]
            userdata_dir = find_userdata_subdir(install_dir)

    exe_path: Optional[Path] = None
    if config.exe_path:
        candidate = Path(config.exe_path)
        if candidate.is_file():
            exe_path = candidate
    if exe_path is None and install_dir is not None:
        exe_path = discover_exe(install_dir)

    return QmtLocations(
        install_dir=install_dir,
        userdata_dir=userdata_dir,
        exe_path=exe_path,
    )


__all__ = [
    "QmtLocations",
    "QMT_EXE_NAMES",
    "QMT_USERDATA_SUBPATHS",
    "discover_exe",
    "find_userdata_subdir",
    "iter_drive_roots",
    "locate_qmt",
    "looks_like_qmt_install",
    "scan_qmt_install_paths",
]
