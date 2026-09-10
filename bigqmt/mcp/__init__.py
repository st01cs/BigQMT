"""BigQMT MCP 服务：把 QMT HTTP API 暴露为 MCP (Model Context Protocol) 服务。

分层：
- `config`：运行配置（监听地址/端口、QMT 后端地址与 Token、MCP 侧 Bearer 鉴权）；
- `client`：QMT HTTP API 客户端（`QMTApiError` 统一表达后端失败）；
- `server`：FastMCP 实例与 56 个工具 + 2 个资源（需要 `fastmcp`，见 extra `mcp`）；
- `cli` / `__main__`：命令行入口。

设计约定：本包顶层不导入 `fastmcp`，因此 `import bigqmt.mcp` 在未安装 extra 时也可用；
只有 `bigqmt.mcp.server` 需要 `fastmcp`。
"""

from bigqmt.mcp.config import (
    McpConfig,
    McpConfigError,
    config_from_mapping,
    is_loopback,
    load_mcp_config,
)

__version__ = "1.0.0"

__all__ = [
    "McpConfig",
    "McpConfigError",
    "config_from_mapping",
    "is_loopback",
    "load_mcp_config",
    "__version__",
]
