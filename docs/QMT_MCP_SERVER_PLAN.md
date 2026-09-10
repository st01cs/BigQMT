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

### 接口覆盖补齐与交易接口移除（2026-09-10 第二批）

以 QMT 自带 `_PyContextInfo.py`（类 `__PyContext`）为基准做了三方比对：
ContextInfo 113 个方法 / 后端 108 条路由 / MCP 56 个工具，缺口分两类处理后：

- **A 类（后端已有路由，仅缺 MCP 工具）**：新增 21 个只读工具——
  策略上下文 `get_context_info`（一次读 10 个 context 属性）、`get_account_status`、
  扩展数据/因子 `get_ext_data` / `get_ext_data_rank` / `get_factor_value` / `get_factor_rank`、
  标的判断 `is_last_bar` / `is_new_bar` / `is_suspended_stock` / `is_sector_stock` /
  `is_typed_stock` / `get_industry_name_of_stock`、只读交易查询 `get_order_deal` /
  `get_trade_detail_data` / `get_last_order_id` / `get_value_by_order_id`、
  打新与两融 `get_ipo_data` / `get_new_purchase_limit` / `get_debt_contract` /
  `get_assure_contract` / `get_enable_short_contract`。
- **B 类（ContextInfo 有方法、后端无路由）**：新增 30 个只读工具，后端用**白名单通用通道**
  `/api/data/query` 承载（`READONLY_CTX_METHODS`），避免为每个方法各写一个 handler；
  白名单只含查询类方法，任何下单/撤单/任务控制/参数设置方法都不在其中。
- **交易类接口全部移除**：后端删除 24 个 handler 与 24 条路由（买入/卖出/撤单/算法单/
  目标仓位/期货开平仓/任务控制），MCP 删除 `buy_stock` / `sell_stock` / `cancel_all_orders`
  三个工具与对应客户端方法。测试新增 `FORBIDDEN_TOOLS` 防回归断言。

最终：后端 85 条路由、MCP 88 个工具（53 迁移 + 3 资金流 + 51 只读 − 3 交易 − 16 无用），
全部为只读能力。

### 只读接口实测结果（2026-09-10，实盘策略上下文，标的 601899.SH）

新增的 51 个只读工具逐个实测后的分类：

| 分类 | 数量 | 工具 |
| --- | --- | --- |
| 返回真实数据 | 27 | account_status、context_info、is_last_bar/new_bar/suspended/sector/typed、industry_name、trade_detail_data、last_order_id、ipo_data、last_close、close_price、market_data_ex_ori、float_caps、holder_num、is_stock/future/fund、stock_type、net_value、product_asset_value/share/init_share、commission、slippage、subscribe_whole_quote |
| 接口通、本次查询为空 | 6 | order_deal、value_by_order_id、new_purchase_limit、debt/assure/enable_short_contract |
| 本机无数据或需入参 | 15 | ext_data、ext_data_rank、factor_value、factor_rank（值 null）、finance、raw_financial_data、ETF_list、option_undl、back_test_index、smallcap、midcap、largecap、turn_over_rate（NaN）、load_stk_list、load_stk_vol_list |
| 运行时不存在 | 1 | stockcode_in_rzrk（白名单内但 ctx 无此方法，返回明确 503） |
| 已移除 | 2 | get_scale_and_rank、get_scale_and_stock（调用后策略线程崩溃） |

顺带修掉的两个隐患：

- 查询通道返回 NaN/Infinity 时 `json.dumps` 会输出裸 `NaN`，不是合法 JSON——已加
  `sanitize_json` 统一转 null（清理后按空数据返回 503）。
- 端口 10086 被上一个已停止策略的残留 socket 占用时，重启策略只会留下 WinError 10048
  traceback——`init()` 现在启动前预检端口并打印可执行指引。

### 财报数据打通（2026-09-10 第四批）

补齐本地财务数据后，接口仍返回空，最终定位为**字段名格式错误**：

- 正确格式是 `表名.字段名`（`ASHAREINCOME.net_profit_incl_min_int_inc`），
  中文写法 `利润表.净利润` 同样可用；表名共 5 个：`ASHAREBALANCESHEET`（资产负债表）、
  `ASHAREINCOME`（利润表）、`ASHARECASHFLOW`（现金流量表）、`CAPITALSTRUCTURE`（股本表）、
  `PERSHAREINDEX`（主要指标）。
- 原仓库脚本里写的 `epspetters,netprofit,qoipetters,totaloperaterevenue` 是无效字段名，
  这也是此前 `get_financial_data` 一直失败的原因。
- 已在 MCP 侧新增资源 `qmt://info/finance_fields`（5 张表 / 65 个字段的中英对照），
  并在 `get_financial_data`、`get_raw_financial_data` 的入参描述里写明格式。

实测（601899.SH，report_type=report_time）：营业总收入 1941.78 亿、净利润 498.13 亿、
归属净利润 391.70 亿、资产总计 5413.54 亿、负债合计 2682.67 亿、经营现金流净额 554.72 亿、
基本每股收益 1.4730、每股净资产 7.5954、ROE 20.01%、销售毛利率 37.75%、
资产负债率 49.55%、净利润同比 +73.90%。

仍未打通：`get_factor_data` / `get_ext_data` / `get_factor_value`（EP 因子与扩展数据域
在实盘上下文中仍返回空，需要另找命名规则或数据域）。

### 移除本机无法取数的工具（2026-09-10 第五批）

补充下载高级数据后复测，以下 14 个工具仍恒为空，已从 MCP 工具集与后端白名单中移除
（工具数 102 → 88），避免 LLM 客户端反复尝试必然失败的接口：

| 类别 | 工具 | 原因 |
| --- | --- | --- |
| 需要"当前 K 线上下文" | `get_ext_data`、`get_ext_data_rank`、`get_factor_value`、`get_factor_rank`、`get_factor_data`、`get_turn_over_rate` | 本策略只有 `init()` + IOLoop，没有 handlebar 循环，恒返回 null / rank 0 / NaN |
| 运行时无此方法 | `get_finance`、`get_smallcap`、`get_midcap`、`get_largecap`、`stockcode_in_rzrk` | `_PyContextInfo.py` 里有定义，但实盘 `ContextInfo` 对象上没有 |
| 需特定上下文 | `get_back_test_index`、`get_option_undl` | 回测上下文 / 有效期权标的 |
| QMT 自身缺陷 | `get_ETF_list` | `_PyContextInfo.py:571` 调用未定义的全局 `get_etf_list` |

判定依据：同类接口中凡**显式传时间或索引**的都能用（`get_close_price` 传 timetag、
`get_net_value` 传 barpos、`get_commission` 无上下文依赖），说明不是数据缺失而是缺少
bar 上下文。若要恢复第一类工具，需要给策略加最小 `handlebar`（订阅标的、每根 bar 更新
上下文），届时按上表第一行逐个加回即可。

## 6. 验收标准

- `python -m unittest discover -s tests -t .` 全绿，既有 118 个用例不回归。
- `initialize` 协商 `2025-06-18`；`tools/list` 88 个、`resources/list` 3 个。
- `get_stock_name('600000.SH')` 返回「浦发银行」；`get_realtime_quote(['600000.SH'])` 返回真实 tick；
  `get_instrument_detail` 不再 404；`get_trading_dates` 返回真实交易日。
- 后端失败时工具结果为 `isError=true`。

## 7. 运行与测试约定

- 服务：`python -m bigqmt.mcp`（等价 `bigqmt-mcp`），默认 `127.0.0.1:9000`。
- 局域网：`python -m bigqmt.mcp --host 0.0.0.0 --allow-remote --auth-token <token>`，
  客户端需带 `Authorization: Bearer <token>`。
- 本机若尚未安装 `mcp` extra（无网络），可用 QMT 侧的虚拟环境运行 MCP 相关测试：
  `QMT/mcp_server/.venv/Scripts/python.exe -m unittest discover -s tests -t .`。
