"""MCP 服务配置：读取 .env / 环境变量。

设计说明：
- 解析函数保持纯净（`config_from_mapping`），便于单元测试；
- 默认只监听回环地址；绑定非回环地址需显式 `allow_remote=True`；
- `auth_token` 非空时，MCP HTTP 入口要求 `Authorization: Bearer <token>`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from bigqmt.config import parse_env_file

try:  # python-dotenv 为可选依赖（与 bigqmt.config 保持一致）
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    _load_dotenv = None


class McpConfigError(ValueError):
    """MCP 配置解析/校验错误。"""


_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_FALSE_VALUES = {"0", "false", "no", "off", "n"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


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
    if text in _FALSE_VALUES:
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


def is_loopback(host: str) -> bool:
    """判断监听地址是否仅本机可见。"""
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


@dataclass(frozen=True)
class McpConfig:
    """MCP 服务运行配置（不可变快照）。"""

    name: str = "QMT Trading Server"
    version: str = "1.0.0"
    instructions: str = (
        "提供迅投 QMT 量化交易系统的完整功能：行情查询、账户管理、股票/期货/期权交易。"
        "所有交易操作需确认后再执行。"
    )
    host: str = "127.0.0.1"
    port: int = 9000
    allow_remote: bool = False
    auth_token: Optional[str] = None
    qmt_base_url: str = "http://127.0.0.1:10086"
    qmt_token: str = "123456789"
    request_timeout: float = 10.0

    def with_updates(self, **kwargs: Any) -> "McpConfig":
        """返回替换指定字段后的新配置（frozen dataclass 不可原地修改）。"""
        return McpConfig(**{**self.__dict__, **kwargs})

    @property
    def binds_remote(self) -> bool:
        """是否绑定了非回环地址。"""
        return not is_loopback(self.host)

    def validate(self) -> "McpConfig":
        """校验配置，非法时抛 `McpConfigError`。"""
        if self.binds_remote and not self.allow_remote:
            raise McpConfigError(
                f"监听地址 {self.host} 对局域网开放，需要显式 allow_remote（CLI: --allow-remote）"
            )
        if self.port <= 0 or self.port > 65535:
            raise McpConfigError(f"非法端口: {self.port}")
        if not self.qmt_base_url.startswith(("http://", "https://")):
            raise McpConfigError(f"非法 QMT 地址: {self.qmt_base_url}")
        return self


def config_from_mapping(
    mapping: Mapping[str, Any], base: Optional[McpConfig] = None
) -> McpConfig:
    """从键值映射（环境变量/.env）解析 McpConfig。"""
    current = base or McpConfig()
    get = mapping.get
    return McpConfig(
        name=_to_str(get("QMT_MCP_NAME"), current.name),
        version=current.version,
        instructions=_to_str(get("QMT_MCP_INSTRUCTIONS"), current.instructions),
        host=_to_str(get("QMT_MCP_HOST"), current.host),
        port=_to_int(get("QMT_MCP_PORT"), current.port),
        allow_remote=_to_bool(get("QMT_MCP_ALLOW_REMOTE"), current.allow_remote),
        auth_token=_to_str(get("QMT_MCP_AUTH_TOKEN")) or None,
        qmt_base_url=_to_str(get("QMT_HTTP_BASE_URL"), current.qmt_base_url),
        qmt_token=_to_str(get("QMT_HTTP_TOKEN"), current.qmt_token),
        request_timeout=_to_float(get("QMT_MCP_REQUEST_TIMEOUT"), current.request_timeout),
    )


def _find_env_file(start: Optional[Path] = None) -> Optional[Path]:
    """从 start（默认 cwd）向上逐级寻找 .env（与 bigqmt.config 行为一致）。"""
    base = Path(start or Path.cwd()).resolve()
    for directory in [base, *base.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_mcp_config(env_file: Optional[os.PathLike] = None) -> McpConfig:
    """加载 .env（可选）后，从进程环境变量解析 McpConfig。"""
    target = Path(env_file) if env_file is not None else _find_env_file()
    if target is not None and Path(target).is_file():
        if _load_dotenv is not None:
            _load_dotenv(Path(target))
        else:  # pragma: no cover - 依赖缺失时的降级路径
            for key, value in parse_env_file(target).items():
                if key not in os.environ:
                    os.environ[key] = value
    return config_from_mapping(os.environ)


__all__ = [
    "McpConfig",
    "McpConfigError",
    "config_from_mapping",
    "is_loopback",
    "load_mcp_config",
]
