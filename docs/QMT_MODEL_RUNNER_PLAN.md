# BigQMT QMT 模型自动运行 设计 Plan（模式 B：客户端内置「模型交易」）

> 状态：设计草案（未编码）
> 背景：用户现有的 HTTP 服务模型（`bigqmt/service/http.py`，入口 `init(ContextInfo)`）是
> QMT **内置模型脚本**，加密存储于 `<QMT>\python\`，只能在客户端「模型交易」内运行，
> 无法被外部 pythonw 进程执行（还叠加 `http.py` 遮蔽标准库 `http`、无 `__main__`）。

## 1. 目标与边界

- 目标：BigQMT 使 QMT 进入 READY 后，自动在客户端「模型交易」中**载入并运行**用户指定的
  明文 Python 模型；客户端掉线自动重启并再次 READY 后模型能重跑；保持 **0 miniQMT 依赖**。
- 关键收益：模型跑在客户端模型引擎内 → 有 `ContextInfo`、直接用客户端数据通道（xtdata），
  **不需要外部行情服务**；也解决外部 pythonw 无法执行内置模型的问题。
- 边界：
  - 不直接改写 `<QMT>\python\` 里的加密模型文件；
  - 不处理模型回测；
  - GUI 控件差异按「可配置定位 + 人工兜底」处理。

## 2. 为什么能绕开外部 pythonw 的坑

| 外部策略（A 模式，现状） | 内置模型（B 模式，本 Plan） |
| --- | --- |
| 独立进程，需自带 `__main__` | 客户端调用 `init(ContextInfo)` 等回调 |
| 需要外部 xtquant 服务（miniQMT） | 模型引擎在客户端内，数据直连，无需 miniQMT |
| `http.py` 遮蔽标准库会崩 | 客户端内加载无此问题 |

## 3. 先决侦察 S0（必须先做）

QMT「模型交易」GUI 因券商/版本差异大，动手前先做一次**控件侦察 + 界面树 dump**
（pywinauto UIA backend）：

- 菜单路径 / 面板名称（国金版：策略交易 → 模型交易？）；
- 「新建模型 / 导入 / 打开」按钮与编辑器；是否支持**从文件导入明文 .py**，还是只能在
  编辑器内粘贴；
- 「运行 / 停止」按钮、模型运行状态指示（运行中 / 已停止）、运行窗口 / 日志位置；
- 加密存储是否在「导入 / 新建」后由客户端自动处理（我们不写 python 目录）。

产物：一份可复现的 GUI 操作序列参数表（控件名 / 查找策略），输出到 `docs/`。

## 4. 运行时序

```text
CLI: start --model <明文.py>（或 .env QMT_MODEL_SOURCE）
 -> QmtManager.start() 到 READY（现有逻辑）
 -> 触发 on_ready（复用现有钩子）
 -> ModelSupervisor：
    1) 打开「模型交易」面板
    2) 目标模型若不存在 -> 新建并载入明文（文件导入 或 编辑器粘贴）
    3) 若已在运行 -> 跳过；否则点击运行
    4) 轮询运行态确认（按钮状态 / 运行日志出现服务启动行）
 -> 心跳掉线 -> QMT 自动重启恢复 -> 再次 READY -> 重复 2~4
用户 Ctrl+C / model stop -> 点「停止」并清理
```

## 5. 模块与配置

- 新增 `bigqmt/core/qmt/_model_runner.py`：
  - `ModelSpec`（dataclass）：`source`（明文 .py 路径）、`name`（模型名，默认取文件名
    去扩展名，建议避免 `http` 这类标准库名，如 `qmt_http_server`）、`start_timeout`、
    `restart`、`max_restarts`；
  - `ModelRunner`：封装 GUI 动作（打开 / 新建 / 粘贴 / 运行 / 停止 / 查状态），
    **全部动作走可注入接口**（真实 pywinauto / 测试 Fake）；
  - `ModelSupervisor`：订阅 QmtManager `on_ready` / 状态监听，掉线先停模型、恢复再跑
    （与 `StrategySupervisor` 对称）。
- 配置新增：`QMT_MODEL_ENABLED`、`QMT_MODEL_SOURCE`、`QMT_MODEL_NAME`、
  `QMT_MODEL_RESTART`、`QMT_MODEL_MAX_RESTARTS`、`QMT_MODEL_LOG`。
  客户端内日志难以外取时，用「运行态轮询 + 服务端口探活」代替（如 HTTP 模型探
  `127.0.0.1:PORT/health`）。
- 复用 `_auto_login.py` 已沉淀的窗口重连 / 控件查找封装。

## 6. CLI / Python API

- CLI：`start --model <source.py>`（前台监督，Ctrl+C 停止模型）与
  `model start|stop|status|restart`；
- Python：`ModelSupervisor(manager, ModelRunner(...)).start()`。

## 7. 里程碑（遵循 AGENTS.md：每步测试 + 原子 commit）

| 阶段 | 内容 | 测试 / 验收 |
| --- | --- | --- |
| M0 | GUI 侦察 + 控件定位表（真机 dump） | 输出参数表到 docs |
| M1 | ModelSpec / config 解析 + GUI 动作接口与 Fake | 单测 |
| M2 | 打开 / 新建 / 载入明文 / 粘贴实现 | Fake + 真机逐步 |
| M3 | 运行 / 停止 / 运行态检测 + 监督 / 自动重启 | Fake 状态机单测 |
| M4 | 真机验收：READY -> 自动载入并运行 `qmt_http_server` 模型 -> HTTP 端口探活 | `BIGQMT_QMT_MODEL_E2E=1` |
| M5 | README / .env.example / 文档更新 | docs commit |

## 8. 主要风险

- 客户端「模型交易」GUI 控件因版本 / 券商差异 -> 先做 S0 侦察并参数化定位，失败保留窗口
  人工兜底；
- 明文模型导入后客户端自动加密落盘，我们**不去直接读写**加密文件，只通过 GUI 让客户端
  自己处理；
- 模型运行态判定：优先「运行中」控件状态 + 服务端口探活（对 HTTP 型模型），日志仅辅助；
- 模型与外部 strategy 的共存 / 互斥：同一会话建议二选一，避免重复启动。
