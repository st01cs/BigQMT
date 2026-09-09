import unittest

import bigqmt
import bigqmt.core.qmt as qmt


class SmokeTest(unittest.TestCase):
    def test_version_available(self):
        self.assertIsInstance(bigqmt.__version__, str)
        self.assertTrue(bigqmt.__version__)

    def test_public_api_importable(self):
        for name in (
            "QmtConfig",
            "QmtConfigError",
            "QmtLocations",
            "parse_tasklist_csv",
            "find_process_pid",
            "any_process_running",
            "scan_qmt_install_paths",
            "locate_qmt",
        ):
            self.assertTrue(hasattr(qmt, name), f"缺少公开导出: {name}")


if __name__ == "__main__":
    unittest.main()
