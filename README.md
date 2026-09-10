# BigQMT

原生 QMT（完整版客户端）的启动与管理工具：自动发现 QMT 安装 → 拉起客户端 → GUI
自动登录 → 状态机管理到 **READY（进程存活 + 行情数据连通）**，并提供 CLI、装饰器、
上下文管理器与心跳/自动重启；READY 后可自动拉起并监督你指定的策略程序。

> 定位：BigQMT 只面向**原生 QMT（完整版客户端）**，不包含 miniQMT 直连与信号桥接。
> 默认是「数据/登录」模式；真单交易会话（XtQuantTrader）为可选项（P4，默认关闭）。

## 功能特性

- 🔍 **安装自动发现**：以 `userdata_mini` / `userdata` 为特征扫描本机，定位客户端可执行文件
- 🚀 **客户端启动**：拉起原生 QMT 客户端进程
- 🔑 **GUI 自动登录**：pywinauto 填写密码并提交，`xtquant.is_connected()` 优先判定登录态，
  窗口启发式兜底；失败保留窗口转人工
- ♻️ **生命周期管理**：显式状态机（STOPPED/STARTING/LOGIN/READY/DISCONNECTED/ERROR），
  心跳探活 + 自动重启（有次数上限）
- 🧩 **多种接入方式**：CLI、`@require_ready()` 装饰器、`with QmtManager()` 上下文、单例便捷函数
- 📋 **CLI**：`start / stop / restart / login / status`
- 🎯 **策略自动运行**：READY 后自动拉起指定策略命令行；QMT 掉线/恢复时自动停/重启策略，
  崩溃按上限自动重启（`once` / `supervise` 两种模式）

里程碑状态：

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| P0/P1 | 脚手架、配置、路径发现、进程检测 | ✅ |
| P2 | QmtManager 状态机、心跳/自动重启、CLI | ✅ |
| P3 | 原生客户端 GUI 自动登录 | ✅（真实 QMT 已验收） |
| P4（可选） | XtQuantTrader 交易会话 | ⏳ 未实现，由 `QMT_TRADING_REQUIRED=true` 启用 |
| 策略 S1~S3 | 策略自动运行（config / 运行器 / 监督编排 / CLI） | ✅ |
| 策略 S4 | 真机机制 E2E（自动拉起哑策略写心跳，无 miniQMT） | ✅ |

## 环境要求

- Windows（QMT 客户端仅支持 Windows）
- 已安装原生 QMT（完整版）客户端，如 `国金证券QMT交易端`
- Python 3.10+（MCP 服务依赖 `fastmcp 4.x`，其要求 `>=3.10`）
- 运行时依赖：`xtquant`（随 QMT 客户端/Python 环境提供，见下文"依赖说明"）
- GUI 自动登录（可选，强烈建议）：`pywinauto`、`pyautogui`

### 依赖说明

`xtquant` 不是 PyPI 包，需从 QMT 客户端环境获取（QMT 安装目录或券商提供的完整版），
使其可 `import xtquant` 即可。GUI 自动登录依赖可随项目一起安装：

```bash
pip install "bigqmt[auto_login]"

# 可选：MCP 服务依赖（fastmcp + requests）
pip install "bigqmt[mcp]"
```

## 安装

```bash
git clone git@github.com:st01cs/BigQMT.git
cd BigQMT

python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1        # PowerShell
pip install -e .

# 可选：GUI 自动登录依赖
pip install -e ".[auto_login]"

# 可选：MCP 服务依赖
pip install -e ".[mcp]"

# 可选：开发/测试依赖（pytest 等）
pip install -e ".[dev]"
```

## 配置 `.env`

复制 `.env.example` 为 `.env` 并填写（`.env` 已被 .gitignore 排除，不会入库）：

```bash
copy .env.example .env
```

关键配置项：

| 变量 | 说明 | 必填 |
| --- | --- | --- |
| `QMT_EXE_PATH` | 原生客户端 exe 完整路径（如 `D:/国金证券QMT交易端/bin.x64/XtItClient.exe`） | 自动发现失败时必填 |
| `QMT_USERDATA_PATH` / `QMT_DATA_DIR` | xtquant 连接所需数据目录（`userdata_mini` / `userdata`） | 交易/精确连接时建议 |
| `QMT_ACCOUNT_ID` | 资金账号 | 交易时必填 |
| `QMT_PASSWORD` | 登录密码（自动登录使用；留空则等待人工登录） | 自动登录时必填 |
| `QMT_TRADING_REQUIRED` | `false`＝READY 只需登录+数据；`true`＝还需交易通道（P4） | 默认 `false` |
| `QMT_SESSION_ID` | 多进程连接时的唯一会话 ID（留空自动生成） | 可选 |
| `QMT_PROCESS_NAMES` | 进程名白名单（逗号分隔） | 可选，默认 `XtItClient.exe` |
| `QMT_AUTO_RESTART` / `QMT_MAX_RESTARTS` | 心跳自动重启开关与上限 | 可选 |
| `QMT_LOGIN_TIMEOUT` | 单次登录/就绪等待超时（秒） | 可选，默认 60 |
| `QMT_LOGIN_ACTION_DELAY` / `QMT_LOGIN_TYPE_INTERVAL` | 自动登录动作停顿 / 逐字符输入间隔（秒）；密码易填错框时加大 | 可选，默认 0.5 / 0.05 |
| `QMT_STRATEGY_ENABLED` | 启用策略自动运行 | 默认 `false` |
| `QMT_STRATEGY_CMD` | 策略命令行（如 `python D:\strat\main.py --trade`） | 启用时必填 |
| `QMT_STRATEGY_PYTHON` | 策略解释器（需可 `import xtquant`；默认当前解释器） | 可选 |
| `QMT_STRATEGY_MODE` | `once`（退出即结束）/ `supervise`（崩溃重启） | 默认 `supervise` |
| `QMT_STRATEGY_MAX_RESTARTS` / `QMT_STRATEGY_GRACE` | 重启上限 / 停止宽限 | 可选 |

MCP 服务配置（详见 `docs/QMT_MCP_SERVER_PLAN.md`）：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `QMT_MCP_HOST` | MCP 监听地址 | `127.0.0.1`（仅本机） |
| `QMT_MCP_PORT` | MCP 监听端口 | `9000` |
| `QMT_MCP_ALLOW_REMOTE` | 允许绑定非回环地址（对局域网开放） | `false` |
| `QMT_MCP_AUTH_TOKEN` | 非空则要求 `Authorization: Bearer <token>` | 空（不鉴权） |
| `QMT_HTTP_BASE_URL` | QMT 侧 HTTP API 地址 | `http://127.0.0.1:10086` |
| `QMT_HTTP_TOKEN` | 调用 QMT HTTP API 的 `X-Token` | `123456789` |
| `QMT_MCP_REQUEST_TIMEOUT` | 调用 QMT 后端超时（秒） | `10` |

## 快速使用

### 方式一：CLI

```powershell
python -m bigqmt.core.qmt status     # 查看状态
python -m bigqmt.core.qmt start      # 启动 + 自动登录，阻塞到 READY
python -m bigqmt.core.qmt login      # 仅自动登录（客户端需已运行）
python -m bigqmt.core.qmt stop       # 停止管理器（默认不关 QMT 窗口）
python -m bigqmt.core.qmt restart    # 重启
python -m bigqmt.core.qmt -h         # 帮助

# 策略自动运行：READY 后前台拉起并监督策略，Ctrl+C 优雅退出
python -m bigqmt.core.qmt start --strategy "python D:\strat\main.py"
# 或 .env 配置 QMT_STRATEGY_ENABLED=true + QMT_STRATEGY_CMD 后：
python -m bigqmt.core.qmt strategy start|stop|status|restart
```

`start` 成功即 READY（进程存活 + `xtdata.is_connected()`），退出码 0。

> 策略进程注入环境变量 `QMT_USERDATA_PATH` / `QMT_ACCOUNT_ID` / `QMT_SESSION_ID`，
> 策略内直接使用 `xtdata`（数据）或自建 `XtQuantTrader`（下单，需券商开通程序化权限）。

### 方式二：Python API

```python
from bigqmt.config import load_qmt_config
from bigqmt.core.qmt import QmtManager, require_ready

# 单例便捷函数（自动读取 .env）
manager = QmtManager(config=load_qmt_config())
if manager.start(timeout=90):
    print("QMT 已就绪（登录 + 数据连通）")
    print(manager.status())
manager.stop()

# 装饰器：就绪后才执行
@require_ready()
def read_data():
    from xtquant import xtdata
    return xtdata.is_connected()

# 上下文管理器：进入自动 start，退出自动 stop
with QmtManager(config=load_qmt_config()) as mgr:
    ...
```

### 方式二·策略自动运行（Python API）

```python
from bigqmt.config import load_qmt_config
from bigqmt.core.qmt._strategy import StrategyRunner, StrategySpec, StrategySupervisor
from bigqmt.core.qmt import QmtManager

manager = QmtManager(config=load_qmt_config())
runner = StrategyRunner(StrategySpec(command="python D:\\strat\\main.py"))
supervisor = StrategySupervisor(manager, runner)   # 掉线停策略、恢复自动重拉
supervisor.start(timeout=90)
...
supervisor.stop()          # 停止策略 + 管理器（默认不关 QMT 客户端）
```

### 方式三：模块级便捷函数

```python
from bigqmt.core.qmt import ensure_ready, get_qmt_status

if ensure_ready(auto_start=True):
    print(get_qmt_status())
```

## 数据读取

BigQMT 负责把 QMT 带到"已登录 + 数据连通"（READY）。就绪后可直接使用 `xtquant.xtdata`
读取行情/历史数据：

```python
from xtquant import xtdata

assert xtdata.is_connected()
stock_list = xtdata.get_stock_list_in_sector("沪深A股")
```

## 测试与验收

```bash
# 全量单元测试（默认跳过真实环境 E2E）
python -m unittest discover -s tests -t .

# 真实 QMT 环境 E2E（需已配置 .env 且装有 pywinauto/pyautogui）
$env:BIGQMT_QMT_E2E = "1"
python -m unittest tests.test_auto_login_e2e -v

# MCP 协议级 E2E（需 MCP 服务已在运行）
$env:QMT_MCP_E2E = "1"
python -m unittest tests.test_mcp_protocol_e2e -v
```

> 真实环境验收记录见 `docs/QMT_STARTUP_PLAN.md`。

## MCP 服务

把 QMT 的行情/财务/账户查询能力暴露为 MCP 工具（104 个 tools + 2 个 resources），
适合被支持 MCP 的客户端（Claude Desktop、Codex 等）直接调用。

**本服务只提供只读能力**：所有下单、撤单、算法单、期货开平仓、任务控制类接口
（`buy_stock` / `sell_stock` / `cancel_all_orders` / `passorder` 等）已从后端路由与
MCP 工具中整体移除，测试里有专门的防回归断言。

能力分组：

- 行情/行情订阅、财务与因子（`get_finance`、`get_raw_financial_data`、`get_ext_data`、
  `get_factor_value` 等）、股东与股本、分红、龙虎榜、换手率
- 资金流：`get_north_finance_change`（北向资金，市场级）、`get_hkt_statistics` /
  `get_hkt_details`（个股港通统计与逐日明细）
- 标的判断：`is_stock` / `is_future` / `is_fund` / `is_suspended_stock` / `is_sector_stock` 等
- 账户查询：持仓、资金、成交、委托、两融标的、打新数据、账号自检
- 长尾只读接口通过后端白名单 `/api/data/query` 暴露，白名单见
  `bigqmt/service/http.py` 的 `READONLY_CTX_METHODS`

```bash
# 默认仅本机可访问：http://127.0.0.1:9000/mcp
python -m bigqmt.mcp

# 指定端口 / QMT 后端地址
python -m bigqmt.mcp --port 9100 --qmt-url http://127.0.0.1:10086

# 需要局域网访问时：必须显式声明，并强烈建议启用 Bearer 鉴权
python -m bigqmt.mcp --host 0.0.0.0 --allow-remote --auth-token <your-token>
```

### 作为受管进程运行（推荐）

MCP 服务可交给 BigQMT 的进程管理（复用策略运行器：PID 文件 + 启动确认 + 崩溃重启 + 日志）：

```bash
# 启动（后台常驻；默认使用当前解释器，可用 --python 指定带 fastmcp 的解释器）
python -m bigqmt.core.qmt mcp start --python "D:\path\to\python.exe"

# 查看状态 / 停止 / 重启
python -m bigqmt.core.qmt mcp status
python -m bigqmt.core.qmt mcp stop
python -m bigqmt.core.qmt mcp restart
```

日志与 PID：`logs/mcp_server.log`、`logs/mcp_server.pid`。
启动前会提示 QMT 当前状态（未就绪时仅警告，不阻断，可用 `--skip-qmt-check` 关闭）。
Bearer token 通过 `--auth-token` 写入子进程环境变量，不会出现在命令行里。

链路：MCP 客户端 → `bigqmt.mcp`（9000）→ QMT HTTP API（10086，跑在 QMT 内）→ 迅投 QMT。
其中 QMT 侧 API 由 `bigqmt/service/http.py` 提供（部署到 QMT 的 python 目录，以策略方式运行，
使用 QMT 内置 Python 3.6）。

排查提示：

- 账户/资金类工具报错时先查 `GET/POST /api/sys/account_status`（需带 `X-Token`）：
  它返回账号是否已配置、是否连通，并给出修复指引。
- `bigqmt/service/http.py` 的资金账号改为读取环境变量 **`QMT_ACCOUNT_ID`**：
  未配置时资金/持仓/委托接口返回 503 并附提示（不再是无说明的 500）。
  该脚本运行在 QMT 内，改动后需要在 QMT 里**重新加载/重启该策略**才生效；
  并确认环境变量对 QMT 进程可见（系统环境变量或 QMT 策略运行环境）。
- 工具调用失败会以 `isError=true` 返回，错误正文包含后端 URL 与状态码。
- 手工验证脚本：`python scripts/verify_mcp_endpoint.py --url http://127.0.0.1:9000/mcp`。

## 项目结构

```text
BigQMT/
├─ bigqmt/
│  ├─ config.py              # .env / 环境变量解析（含标准库兜底）
│  ├─ mcp/                   # MCP 服务（FastMCP）
│  │  ├─ config.py           # 监听地址/端口/鉴权/QMT 后端配置
│  │  ├─ client.py           # QMT HTTP 客户端 + QMTApiError
│  │  ├─ auth.py             # Bearer 鉴权中间件
│  │  ├─ runner.py           # MCP 服务进程管理（复用 StrategyRunner）
│  │  ├─ server.py           # 56 tools + 2 resources
│  │  └─ cli.py / __main__.py
│  ├─ service/
│  │  └─ http.py             # 部署到 QMT 内的 Tornado API（Py3.6）
│  └─ core/qmt/
│     ├─ _connection.py      # QmtManager 状态机、心跳、装饰器/上下文
│     ├─ _paths.py           # 安装/数据目录自动发现
│     ├─ _process.py         # 进程检测（tasklist）
│     ├─ _auto_login.py      # 原生客户端 GUI 自动登录（pywinauto）
│     ├─ _strategy.py        # StrategySpec / StrategyRunner / StrategySupervisor
│     ├─ cli.py / __main__.py
├─ tests/                    # unittest 测试 + E2E（默认 skip）
├─ scripts/verify_mcp_endpoint.py     # MCP 端点手工验证
├─ docs/QMT_STARTUP_PLAN.md  # 设计 Plan 与验收记录
├─ docs/QMT_STRATEGY_RUNNER_PLAN.md  # 策略自动运行设计 Plan 与验收记录
├─ docs/QMT_MCP_SERVER_PLAN.md       # MCP 迁移 Plan 与验收记录
├─ .env.example
└─ pyproject.toml
```

## 注意事项

- 自动登录会真实操作 QMT 客户端窗口，请在**模拟盘/测试账户**中先验证流程。
- 不同券商/版本客户端界面可能有差异：若自动登录定位不准，重点调整
  `bigqmt/core/qmt/_auto_login.py` 中的密码框/按钮查找策略即可，状态机无需改动。
- 未配置密码时 `start` 会进入"等待人工登录"，窗口保持可见，由你手动完成登录。
- Windows 控制台若显示中文日志乱码，属代码页显示问题，不影响功能与日志内容。
- 真单交易（P4）尚未实现；需先接入 `XtQuantTrader` 会话并把 `QMT_TRADING_REQUIRED` 置 `true`。
- `start --strategy` / `strategy start` 为**前台监督**：进程保持运行直到 Ctrl+C；
  若需后台常驻，请用 `nssm`/计划任务等托管进程。跨进程 `strategy stop` 通过 PID 文件整树终止策略。

## License

[MIT](LICENSE)
