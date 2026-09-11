# BigQMT QMT 启动与管理逻辑设计 Plan

> 状态：P0~P3 已实现并验收（含 S1~S4 策略自动运行），P4 交易会话待接入；过程记录见第 8 节。
> 定位：BigQMT 仅支持原生 QMT（完整版客户端），不做 miniQMT 分支与信号桥接。

## 1. 设计目标与边界

BigQMT 需要一套「看得见 QMT 状态、能拉起 QMT、能自动登录、能保活、能优雅退出」的生命周期管理模块。

设计前提：

- **直接用原生客户端 + xtquant API**，不做信号桥接，也没有 miniQMT 分支；
- 「原生 QMT 已运行」≠ 可用：还必须 **已登录 + xtdata 连通** 才算就绪（
  `QMT_TRADING_REQUIRED=true` 时才额外要求交易通道）。

边界（不做的事）：不下单策略逻辑、不做多券商适配、不处理 miniQMT / `XtMiniQmt.exe`、不识别大 QMT / miniQMT 双模式切换。

## 2. 推荐模块布局

主包建议为 `bigqmt`，路径风格贴近 `core\qmt`：

```text
BigQMT/
├─ bigqmt/
│  ├─ config.py              # 读取 .env / 配置
│  └─ core/qmt/
│     ├─ __init__.py          # 对外导出单例与装饰器
│     ├─ _connection.py       # QmtManager：状态机 + 生命周期主入口
│     ├─ _paths.py            # 安装目录/userdata 发现
│     ├─ _process.py          # 进程/窗口/登录态检测（tasklist + pywinauto）
│     ├─ _auto_login.py       # 原生客户端自动登录
│     ├─ _trader.py           # XtQuantTrader 会话建立 + 账户订阅 + 健康探针
│     └─ cli.py               # python -m bigqmt.core.qmt start|stop|status|restart|login
├─ tests/                     # 单测 + 真实 QMT 的 E2E（标记跳过）
├─ .env.example
└─ pyproject.toml
```

模块也可放在仓库根 `core/qmt/`，内部设计不变。

## 3. 状态机（核心）

QmtManager 用显式状态机管理生命周期，并要求缓存就绪状态前复核，避免「轮询缓存直接返回 True」的假阳性：

```text
STOPPED ─start()→ STARTING ─进程已拉起→ LOGIN ─登录成功+API连通→ READY
   ↑                        │                                    │
   │                        │ 启动失败/超时                       │ 掉线/进程退出
   └──── stop() ←──────── ERROR ←─────────────────── DISCONNECTED
                          │ 自动重试（有次数上限，超限转 ERROR 并通知）
```

- `READY` 定义：原生 QMT 进程存活 + `xtdata.is_connected()==True`（数据通道可用）。
  默认数据/登录模式（`QMT_TRADING_REQUIRED=false`）即以此判定；
  仅当显式开启 `QMT_TRADING_REQUIRED=true` 时才额外要求交易会话 connect + 至少一个账户 subscribe 成功（P4）。
- 每次状态迁移发事件/日志；`status()` 返回 state、pid、login、xtconnected、trader、last_heartbeat。

## 4. 模块要点

### config.py

- 配置项：`QMT_EXE_PATH`（原生客户端 exe，默认补全候选如 `bin.x64\XtItClient.exe`）、`QMT_USERDATA_PATH` / `QMT_DATA_DIR`、`QMT_ACCOUNT_ID`、`QMT_PASSWORD`、`QMT_PROCESS_NAMES`（进程名白名单，防误杀）、`session_id`、自动启动/重启开关与次数上限、`QMT_TRADING_REQUIRED`（交易通道要求开关）、探测间隔。
- 显式配置优先 → 磁盘扫描兜底。

### _paths.py

- 以「目录下存在 userdata_mini」作为识别特征扫描本机（第一层全扫、第二层按关键词进，控制耗时）。
- 额外允许 `userdata`（完整版客户端目录），并校验其中能启动原生客户端。

### _process.py

- 进程检测：`tasklist` / psutil 按 `QMT_PROCESS_NAMES` 匹配，返回 pid；优先用 exe 路径精确连接，降低误判。
- 登录态检测优先级（从严判定）：
  1. `xtdata.is_connected()`（最可靠）
  2. pywinauto 窗口标题：含「登录/密码/账号」→ 未登录；含「委托/持仓/交易/资产/策略」→ 已登录
  3. 存在密码输入框 → 判定为登录界面
- BigQMT 不做「进程存在即视为运行成功」的降级。

### _auto_login.py（不做 miniQMT / 大QMT 双轨检测）

- 原生客户端拉起：`pywinauto Application(backend="uia").start(exe)`，等待窗口加载。
- 登录流程：切到密码框 → 清空 → 键入密码 → 回车触发验证码 → 尝试从窗口文本提取并自动填入 → 回车提交 → 轮询登录态直至超时。
- 自动登录失败/被禁用：**保留可见窗口并进入「等待人工登录」模式**（给 N 秒手动窗口期），避免无头盲操作锁死账号。
- 教训沉淀：操作在独立线程跑、窗口句柄失效时重连、验证码 OCR 仅作可选增强、绝不暴力杀进程。

### _trader.py

- 使用整数 `session_id`（默认按时间戳/随机生成，防多实例碰撞；可配置固定值）创建 `XtQuantTrader(userdata_path, session_id)` → connect → `subscribe(StockAccount(account_id))`。
- 提供 `ping()`（查一次资产/持仓）作为交易通道健康探针。

### _connection.py（QmtManager）

- 单例 + 模块级便捷函数：`get_qmt_manager()`、`status()`、`ensure_ready(auto_start=True, timeout)`、`@require_ready()` 装饰器、`start/stop/restart`、`__enter__/__exit__` 上下文管理。
- 心跳线程：默认 15~30s 探测一次（带抖动）。进程退出或通道掉线 → 按策略自动重启（上限内）→ 仍失败转 ERROR 并通知；健康时才刷新 last_heartbeat。并发安全用 RLock。
- 启动早期对 xtdata 下载方法做全局加锁，防并发下载卡死。

### cli.py

- `python -m bigqmt.core.qmt status|login|start|stop|restart`，退出码 0/非 0 便于脚本判断。

## 5. 关键设计取舍

1. 就绪状态只在心跳确认健康后才缓存，不做「一段时间内直接返回 True」的假阳性缓存。
2. 直接对原生客户端做 GUI 登录 + xtquant 直连，登录健壮性要求更高（含人工兜底）。
3. 原生客户端登录界面更重（可能含服务器选择、验证码、行情/交易双登录），登录态判定须多探针组合而非单一窗口标题。
4. 把「已登录可用」收敛为一个 READY 态，供策略/数据层统一依赖，而不是各自再查一遍。

## 6. 分阶段实施（含测试，遵循 AGENTS.md）

- **P0 脚手架**：`pyproject.toml`、包结构、`.env.example`，跑通空包导入。（测试：冒烟）
- **P1 纯逻辑**：config + 路径发现 + 进程检测。路径扫描用临时目录夹具单测；进程检测 mock tasklist。
- **P2 状态机**：QmtManager 迁移逻辑，用假进程/假 xtquant 验证各状态迁移与重启上限。装饰器/上下文/CLI 单测。
- **P3 自动登录集成**：真实 QMT 环境跑 E2E，单测标记 skip；验证码/人工兜底单独验证。
- **P4 交易会话**：_trader 连接 + 订阅 + ping，mock 验证断开重连。
- **P5 收尾**：心跳监控压测、文档、完整测试套件跑绿、按里程碑逐个 commit（feat: …）。

## 7. 主要风险

- 原生客户端 GUI 因券商/版本差异（窗口控件、验证码、登录界面）导致自动登录不稳定 → 以「多探针判定 + 人工兜底 + 可配置流程参数」缓解。
- xtquant 与 QMT 进程「活着但通道断开」的中间态 → 用 ping 级健康探针区分，而不是只查进程。
- 误杀/误重启 QMT → 进程名白名单 + 精确 exe 匹配，重启仅限明确请求或策略内自动恢复。

## 8. 验收记录（2026-09-09）

- P0/P1/P2/P3 单元测试：76 项通过（1 项真实环境 E2E 默认 skip）。
- 真实 QMT 环境 E2E：**已跑通** —— 原生客户端启动 + GUI 自动登录 + xtdata 数据连通，
  运行环境需安装 pywinauto/pyautogui（`pip install bigqmt[auto_login]`）。
- 默认数据/登录模式 READY 已实测可用（`QMT_TRADING_REQUIRED=false`）。
- P4（XtQuantTrader 交易会话）未启用、未实现；需要真单交易时再接入。
