# BigQMT QMT 就绪后自动运行指定策略代码 设计 Plan

> 状态：已实现 S1~S3，S4 真机机制 E2E 已验收（无 miniQMT 依赖）
> 定位：在 `QmtManager` 进入 READY（进程存活 + 已登录/数据连通）后，BigQMT 自动拉起用户
> 指定的策略程序；客户端掉线自动重启并再次 READY 后，策略能随之恢复。

## 1. 目标与边界

- 目标：复用现有 QmtManager 生命周期，增加「策略启动与监督」能力。
  - `QMT_STRATEGY_CMD` 指定要运行的程序（推荐 `python <path>\main.py ...`）；
  - READY 后自动拉起；QMT 掉线自动重启并恢复 READY 后，策略自动随 QMT 恢复；
  - 提供 CLI 与 Python API 两种使用方式。
- 运行形态（保持轻量，不引入重框架）：
  - 策略 = 独立 Python 子进程；
  - PID 落盘、日志、崩溃重启上限；
  - 不做调度器 / 多策略冲突检测，只做「单条策略 + 生命周期随 QMT」。
- 不做：
  - QMT 客户端内置「模型交易」的 GUI 运行（脆、券商差异大，列为远期模式 B）；
  - 多策略编排、资金/持仓冲突检测；
  - 策略内部的选股 / 下单逻辑。
- 关键前提：策略代码默认是**外部 Python 程序**，自己使用 `xtquant`
  （`xtdata` 读数据、`XtQuantTrader` 下单）。BigQMT 是「启动器 + 监护人」，
  不接管策略逻辑。

## 2. 两种运行形态

| 形态 | 说明 | 判定 |
| --- | --- | --- |
| A. 外部 Python 策略进程（推荐/默认） | 用户给一段可执行命令（如 `python D:\strat\main.py`），READY 后由 BigQMT 以子进程拉起，注入 QMT 环境变量 | 与现有架构一致；可测试、可保活 |
| B. QMT 客户端内置运行（远期） | 通过 GUI 自动化打开「模型交易」加载并运行策略 | 依赖登录后控件定位，列为后续可选，不在本期 |

> 策略「要数据」只需默认 READY；策略「要下单」依赖交易通道（见第 6 节）。

## 3. 运行时序

```text
CLI: start --strategy <...>   （或配置 QMT_STRATEGY_CMD 后普通 start）
  -> QmtManager.start() 阻塞到 READY
  -> 触发 on_ready 事件（首次 + 心跳恢复 READY 时都会发）
  -> StrategySupervisor.launch()：校验命令 -> 启动子进程 -> 写入 PID/日志
  -> 状态 strategy=running

  心跳发现 QMT DISCONNECTED -> 自动重启 -> 再次 READY
  -> supervisor 自动重启策略子进程（有上限）

manager.stop() / 用户 Ctrl+C -> 先优雅终止策略 -> 再停管理器（默认不杀 QMT 客户端）
```

## 4. 模块与配置设计

新增 `bigqmt/core/qmt/_strategy.py`（策略启动与监督），并给 `QmtManager` 增加
一个**极小的 on_ready 事件钩子**（不改变状态机职责）：

- `StrategySpec`（dataclass）：`command`、`cwd`、`python`（默认 `sys.executable`）、
  `mode`（`once` / `supervise`）、`max_restarts`、`start_timeout`、`grace_timeout`、
  `log_file`。
- `StrategyRunner`：`launch / stop / restart / status`；子进程用 `subprocess.Popen`；
  Windows 下创建独立进程组以便整树终止；PID 落盘防重复启动；日志转发到文件 + logger。
- `StrategySupervisor`：轮询子进程存活，崩溃按上限自动重启；订阅 QMT on_ready 事件，
  QMT 恢复后重拉策略。
- `QmtManager`：仅新增可选回调注册（如 `add_ready_listener(cb)`，`_mark_ready()` 时
  调用），**默认行为不变**，不破坏既有单测。

`QmtConfig` 新增字段（frozen dataclass + `config_from_mapping` + `.env.example`）：

```text
QMT_STRATEGY_ENABLED      # 默认 false
QMT_STRATEGY_CMD          # 完整命令，如 "python D:\strat\main.py --trade"
QMT_STRATEGY_CWD          # 工作目录（可选）
QMT_STRATEGY_MODE         # once | supervise（默认 supervise）
QMT_STRATEGY_MAX_RESTARTS # 默认 3
QMT_STRATEGY_LOG          # 日志路径（默认 logs/strategy_<ts>.log）
QMT_STRATEGY_START_TIMEOUT # 默认 30s
QMT_STRATEGY_GRACE        # 停止宽限，默认 10s
```

子进程注入环境：`QMT_USERDATA_PATH`、`QMT_ACCOUNT_ID`、`QMT_SESSION_ID`、
`BIGQMT_READY=1`，保证策略无需重复读 `.env`。

## 5. CLI 与 Python API

- CLI：
  - `start` 增加 `--strategy <file|cmd>`（前台跟随）与 `--supervise`；
  - 新增 `strategy start|stop|status|restart`；
  - 退出码 0 / 非 0 供脚本判断。
- Python：

  ```python
  with QmtSession(strategy="python D:\\strat\\main.py") as s:
      ...  # 内部 = manager.ensure_ready() + StrategySupervisor
  ```

  或 `get_qmt_manager().add_ready_listener(...)` 手动接入策略。

## 6. 与 P4（交易通道）的关系

默认 `QMT_TRADING_REQUIRED=false`，READY 只保证「登录 + 数据」。因此分两类策略：

- **数据 / 信号型策略**：本期即可闭环（不依赖 P4）。
- **下单型策略**：策略内部自行 `XtQuantTrader(userdata_path, session_id)` 连客户端下单。
  BigQMT 只负责两点：
  1. 约定独立整数 `session_id`（默认随机时间戳，防多进程 / 多会话冲突；BigQMT 自己的
     P4 会话与策略会话错开）；
  2. 提供 `trading_required` 门控与 **P4 的 `trading_ready()` 探针**，让
     「策略可下单」成为可等待的就绪条件。

> 结论：**先实现「数据型策略自动运行」闭环；下单型策略等 P4（`_trader.py` 建会话 +
> 订阅 + ping）落地后，把「等交易通道就绪再拉策略」接到同一个 supervisor 上**，
> 避免为验证下单而引入未经真实环境验收的 P4。

## 7. 实施里程碑（遵循 AGENTS.md：每步测试 + 原子 commit）

| 阶段 | 内容 | 测试 / 验收 |
| --- | --- | --- |
| S1 | config + `StrategySpec` 解析、默认值、命令拼装 | ✅（单测：config 5 + strategy 9） |
| S2 | `StrategyRunner` 子进程控制 + 监督 + 日志，注入 FakeSpawner | ✅（单测 12：状态迁移/重启上限/终止顺序） |
| S3 | QmtManager on_ready 钩子 + `StrategySupervisor` + CLI `start --strategy` / `strategy *` | ✅（单测 15：钩子/监督编排/CLI） |
| S4 | 真实 QMT E2E：启动 -> READY -> 自动拉起哑策略 -> 确认进程存活并写心跳 | ✅ 真机验收（`BIGQMT_QMT_STRATEGY_E2E=1`） |
| S5 | `.env.example` / README / 文档更新 + 完整套件跑绿 | ✅（docs commit） |
| S6（可选） | P4 `trading_ready` 门控接入 supervisor（下单型策略） | ⏳ 待 P4 落地 |

## 9. 验收记录（2026-09-09）

- 单元测试全绿：117 项通过，1 项真实环境 E2E 默认跳过。
- S4 真机验收（2026-09-09，无 miniQMT）：BigQMT 拉起全量客户端 + GUI 自动登录到 READY，
  自动运行哑策略（`tests/e2e_strategy_dummy.py`），确认 XtItClient 进程存活并写出心跳文件。
- **已知限制**：外部 Python 的 `xtdata`/`XtQuantTrader` 需 miniQMT 提供的本地量化服务；
  BigQMT 明确不引入 miniQMT 依赖，故哑策略不读外部行情数据，只验收启动/运行机制本身。

## 8. 主要风险

- 策略解释器需 `import xtquant`，可能与 BigQMT 运行环境不同 -> 用
  `QMT_STRATEGY_PYTHON` 显式指定。
- Windows 子进程终止只杀父不杀孙 -> 独立进程组 + `taskkill /T` 兜底。
- QMT 自动重启 -> 策略重启可能形成连锁抖动 -> 重启限次 + 冷却时间。
- 全量版客户端「外部程序下单」是否被券商允许、是否需要客户端内启用程序化 / 模型交易
  -> 在真实账户先做模拟单验收，纳入风险清单。
