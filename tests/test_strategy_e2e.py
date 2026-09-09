"""S4 真机 E2E：QMT READY -> 自动拉起策略 -> 监督（默认跳过，需真实环境）。

启用方式（在装有原生 QMT 客户端、已配 .env 的 Windows 机器上）：
    $env:BIGQMT_QMT_STRATEGY_E2E = "1"
    & <解释器含 pywinauto> -m unittest tests.test_strategy_e2e -v

说明：
- 不依赖 xtquant / miniQMT：哑策略只确认全量客户端进程存活并写心跳文件，
  用于验收「READY -> 自动运行策略 -> 监督」机制本身；
- 数据通道（xtdata）真机读回属已知限制：外部 xtquant 需要 miniQMT 提供的本地
  量化服务，而 BigQMT 明确不引入 miniQMT 依赖。
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from bigqmt.config import load_qmt_config
from bigqmt.core.qmt import QmtManager
from bigqmt.core.qmt._strategy import StrategyMode, StrategyRunner, StrategySpec, StrategySupervisor


@unittest.skipUnless(
    os.getenv("BIGQMT_QMT_STRATEGY_E2E") == "1",
    "真机 S4 E2E：设置 BIGQMT_QMT_STRATEGY_E2E=1 启用",
)
class StrategyRunnerE2ETest(unittest.TestCase):
    def test_ready_launches_strategy_and_writes_heartbeat(self):
        config = load_qmt_config()
        self.assertTrue(
            config.exe_path and config.password,
            "E2E 需要配置 QMT_EXE_PATH 与 QMT_PASSWORD",
        )

        dummy = Path(__file__).with_name("e2e_strategy_dummy.py")
        tmp = tempfile.mkdtemp(prefix="bigqmt_s4_")
        heartbeat = os.path.join(tmp, "heartbeat.txt")

        manager = QmtManager(config=config)
        runner = StrategyRunner(
            StrategySpec(
                command=f'python "{dummy}" --out "{heartbeat}" --timeout 60',
                mode=StrategyMode.ONCE.value,
            )
        )
        supervisor = StrategySupervisor(manager, runner)

        try:
            self.assertTrue(
                supervisor.start(timeout=180),
                f"QMT 就绪/策略启动失败: {supervisor.status()['last_error']}",
            )
            # 等待哑策略确认客户端进程并写心跳文件
            deadline = time.time() + 90
            while time.time() < deadline and not os.path.exists(heartbeat):
                time.sleep(1)
            self.assertTrue(
                os.path.exists(heartbeat), "哑策略未在期限内写出心跳文件"
            )
            with open(heartbeat, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("qmt_process_ok=True", content, content)
        finally:
            # 停止策略 + 管理器；默认不关闭 QMT 客户端
            supervisor.stop(close_client=False)


if __name__ == "__main__":
    unittest.main()
