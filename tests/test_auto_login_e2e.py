"""原生 QMT 自动登录 E2E（默认跳过，需真实环境）。

启用方式（在装有原生 QMT 客户端的 Windows 机器上）：
    $env:BIGQMT_QMT_E2E = "1"
    python -m unittest tests.test_auto_login_e2e -v

前提：
- 已安装 pywinauto（pip install bigqmt[auto_login]）；
- 本机可导入 xtquant；
- 配置了 QMT_EXE_PATH 与 QMT_PASSWORD（见 .env.example）。
"""

import os
import unittest

from bigqmt.config import load_qmt_config
from bigqmt.core.qmt._auto_login import NativeQmtAutoLogin


@unittest.skipUnless(
    os.getenv("BIGQMT_QMT_E2E") == "1",
    "真实 QMT E2E：设置 BIGQMT_QMT_E2E=1 启用",
)
class NativeQmtAutoLoginE2ETest(unittest.TestCase):
    def test_login_reaches_logged_in(self):
        config = load_qmt_config()
        self.assertTrue(
            config.exe_path and config.password,
            "E2E 需要配置 QMT_EXE_PATH 与 QMT_PASSWORD",
        )
        alogin = NativeQmtAutoLogin(
            exe_path=config.exe_path,
            password=config.password,
            data_dir=config.userdata_path,
        )
        ok = alogin.login(restart=False, timeout=90)
        if not ok:
            self.fail(f"自动登录失败: {alogin.last_error}")


if __name__ == "__main__":
    unittest.main()
