# QMT MCP Server 迁移设计 Plan

将 `QMT/mcp_server`（独立脚本形态）迁移进 BigQMT，成为 `bigqmt.mcp` 包，
并与 `bigqmt.service.http`（QMT 侧 Tornado HTTP API）形成一条可验证的链路：

```
MCP 客户端 ──HTTP/SSE──> bigqmt.mcp (127.0.0.1:9000)
                              │ X-Token
                              ▼
                    QMT HTTP API (127.0.0.1:10086，跑在 QMT 内)
                              │
                              ▼
                       迅投 QMT / XtItClient
```

## 1. 迁移前的现状

| 项 | 源（QMT/mcp_server） |
| --- | --- |
| 入口 | `qmt_mcp_server.py`（单文件 1362 行） |
| 协议 | FastMCP `mcp.run(transport="http")` |
| 工具 | 53 个 `@mcp.tool` + 2 个 `@mcp.resource` |
| 配置 | 文件头硬编码 `TOKEN` / `QMT_BASE_URL` / `MCP_HOST` / `MCP_PORT` |
| 错误处理 | `_req` 吞掉异常并返回 `{"error": ...}`，MCP 结果 `isError=false` |
| 测试 | `test_mcp.py`（print 冒烟，断言不成立）、`test_tool_params.py`（手搓 SSE） |
| 依赖 | `fastmcp>=2.0.0`、`requests>=2.31.0`（无版本上限） |

## 2. 已确认的决策

1. **Python 下限提到 `>=3.10`**：实际运行的是 `fastmcp 4.0.3`，其 `Requires-Python` 为 `>=3.10`。
2. **依赖限定 `fastmcp>=4.0,<5`**：只在实际验证过的 4.x 上运行，不做跨大版本兼容。
3. **默认只监听 `127.0.0.1`**；需要局域网访问必须显式 `--allow-remote`，并支持 `--auth-token` 启用 Bearer 鉴权。

## 3. 接口契约审计（迁移的实际价值）

迁移过程中静态比对了 MCP 客户端发出的 payload key 与 QMT 后端 handler 读取的 key，结论：

| # | 现象 | 位置 | 修法 |
| --- | --- | --- | --- |
| 1 | `get_instrument_detail` 恒 404 | 调用 `/api/data/instrument_detail` | 改为 `/api/data/instrumentdetail` |
| 2 | `get_realtime_quote` 恒 500 | 传 list，后端 `stocks.split(',')` | 发送前 join 成逗号字符串 |
| 3 | `get_market_extended` 取不到数据 | 传 `stocks`，后端读 `stock_code` | 改发 `stock_code`（逗号字符串） |
| 4 | `get_market_data` 取不到数据 | 同上，且 `fields` 传 list | 改发 `stock_code`，`fields` join |
| 5 | `get_trading_dates` 恒空 | 把股票代码塞进 `market` 形参 | 新增 `market`（默认从代码推导，回退 `SH`） |
| 6 | 后端失败伪装成功 | `_req` 吞异常 | 抛 `QMTApiError`，由 FastMCP 标记 `isError=true` |

已知的**后端侧**限制（不在本次迁移范围内，需单独处理）：

- `bigqmt/service/http.py` 的 `ACCOUNT_ID` 仍是占位符 `'你的QMT账号'`，导致
  `get_total_assets` / `get_available_funds` 返回 500、`get_positions` 返回空。
- `/api/data/history_data` 的后端实现（`ctx().get_history_data(len, period, field, ...)`）
  不接受股票代码，因此 `get_history_data(stocks=...)` 的 `stocks` 参数实际不生效。

## 4. 目标结构

```
bigqmt/
├─ mcp/
│  ├─ __init__.py     # 包级导出（不导入 fastmcp，保持轻量）
│  ├─ config.py       # McpConfig：host/port/token/qmt_base_url/auth_token/allow_remote
│  ├─ client.py       # QMTClient + QMTApiError（requests 懒加载）
│  ├─ auth.py         # BearerAuthMiddleware（纯 ASGI，无 fastmcp 版本耦合）
│  ├─ server.py       # FastMCP 实例 + 56 tools + 2 resources
│  ├─ cli.py          # 参数解析 + 监听地址校验 + 启动
│  └─ __main__.py     # python -m bigqmt.mcp
└─ service/
   └─ __init__.py     # QMT 侧 Tornado API（部署到 QMT python 目录，Py3.6 运行）
```

## 5. 实施阶段

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0 | 文件搬迁 + 配置化 + 依赖声明 | `python -m bigqmt.mcp` 可启动；工具/资源数量与迁移前一致 |
| P1 | 第 3 节 6 项契约修复 | 单测断言 URL/payload；真机 E2E 不再 404/500 |
| P2 | 测试重写（unittest）+ 文档 | `unittest` 全绿；冒烟脚本走规范 MCP 调用 |
| P3 | 接入 CLI 生命周期（可选） | `bigqmt mcp start/status` 在 QMT 就绪后拉起服务 |

### 实施记录（2026-09-10）

- P0–P2 已完成并提交（`6f01736` / `5dfe64b` / `dedade6` / `fac8b90`）。
- P3 已完成：`bigqmt mcp start|status|stop|restart`（`bigqmt/mcp/runner.py` 复用
  `StrategyRunner` 的 PID 文件、启动确认、崩溃重启与日志），token 通过环境变量传递、
  不落命令行。
- 后端账号问题已修：`bigqmt/service/http.py` 的 `ACCOUNT_ID` 改为读 `QMT_ACCOUNT_ID`
  （`TOKEN`/`PORT` 同理读 `QMT_HTTP_TOKEN`/`QMT_HTTP_PORT`），并新增
  `GET/POST /api/sys/account_status` 与启动自检日志；账号缺失时资金/持仓/委托接口
  返回 503 并附修复指引，而不是笼统的 500。

迁移前后同参数实测（旧 9000 vs 新 9001，同一 QMT 后端）：

| 工具 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `get_instrument_detail` | 404 | 真实合约详情 |
| `get_realtime_quote` | 500 | 真实 tick |
| `get_trading_dates` | `[]` | 5 个交易日 |
| `get_market_extended` | 10s 超时 | 真实 K 线（约 10ms） |
| `get_market_data` | 10s 超时 | 真实数据 |
| `get_total_assets` | 伪成功 | `isError=true` + 状态码 |

待办（依赖 QMT 侧人工操作）：把改好的 `bigqmt/service/http.py` 重新加载进 QMT 策略，
并确保 `QMT_ACCOUNT_ID` 对 QMT 进程可见；在此之前线上后端仍是旧副本
（`/api/sys/account_status` 返回 404 可确认）。

## 6. 验收标准

- `python -m unittest discover -s tests -t .` 全绿，既有 118 个用例不回归。
- `initialize` 协商 `2025-06-18`；`tools/list` 56 个（迁移基线 53 + 资金流 3 个）、`resources/list` 2 个。
- `get_stock_name('600000.SH')` 返回「浦发银行」；`get_realtime_quote(['600000.SH'])` 返回真实 tick；
  `get_instrument_detail` 不再 404；`get_trading_dates` 返回真实交易日。
- 后端失败时工具结果为 `isError=true`。

## 7. 运行与测试约定

- 服务：`python -m bigqmt.mcp`（等价 `bigqmt-mcp`），默认 `127.0.0.1:9000`。
- 局域网：`python -m bigqmt.mcp --host 0.0.0.0 --allow-remote --auth-token <token>`，
  客户端需带 `Authorization: Bearer <token>`。
- 本机若尚未安装 `mcp` extra（无网络），可用 QMT 侧的虚拟环境运行 MCP 相关测试：
  `QMT/mcp_server/.venv/Scripts/python.exe -m unittest discover -s tests -t .`。
