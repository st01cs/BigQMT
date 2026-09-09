import tempfile
import unittest
from pathlib import Path

from bigqmt.config import QmtConfig
from bigqmt.core.qmt._paths import (
    discover_exe,
    find_userdata_subdir,
    locate_qmt,
    looks_like_qmt_install,
    scan_qmt_install_paths,
)


class FindUserdataTest(unittest.TestCase):
    def test_finds_userdata_mini(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "userdata_mini").mkdir()
            self.assertEqual(
                find_userdata_subdir(root), root / "userdata_mini"
            )

    def test_finds_userdata_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "userdata").mkdir()
            self.assertEqual(find_userdata_subdir(root), root / "userdata")

    def test_prefers_userdata_mini_over_userdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "userdata_mini").mkdir()
            (root / "userdata").mkdir()
            self.assertEqual(
                find_userdata_subdir(root), root / "userdata_mini"
            )

    def test_returns_none_without_userdata_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "some_other").mkdir()
            self.assertIsNone(find_userdata_subdir(root))


class LooksLikeInstallTest(unittest.TestCase):
    def test_true_with_userdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "userdata_mini").mkdir()
            self.assertTrue(looks_like_qmt_install(root))

    def test_false_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(looks_like_qmt_install(Path(tmp)))


class ScanInstallPathsTest(unittest.TestCase):
    def test_discovers_nested_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = base / "国金QMT交易端模拟"
            (install / "userdata_mini").mkdir(parents=True)
            results = scan_qmt_install_paths(roots=[base], max_depth=2)
            self.assertIn(install, results)

    def test_keyword_pruning_for_deep_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # 第二层目录不含关键词 -> 不应继续深入
            hidden = base / "普通目录" / "qmt目录" / "userdata_mini"
            (hidden).mkdir(parents=True)
            results = scan_qmt_install_paths(roots=[base], max_depth=2)
            self.assertEqual(results, [])

    def test_no_false_positive_plain_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "random_folder").mkdir()
            results = scan_qmt_install_paths(roots=[base], max_depth=2)
            self.assertEqual(results, [])


class DiscoverExeTest(unittest.TestCase):
    def test_finds_exe_in_bin_x64(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "bin.x64" / "XtItClient.exe"
            exe.parent.mkdir()
            exe.touch()
            self.assertEqual(discover_exe(root), exe)

    def test_finds_exe_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "XtItClient.exe"
            exe.touch()
            self.assertEqual(discover_exe(root), exe)

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(discover_exe(Path(tmp)))


class LocateQmtTest(unittest.TestCase):
    def test_prefers_explicit_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            userdata = base / "userdata_mini"
            userdata.mkdir()
            exe = base / "bin.x64" / "XtItClient.exe"
            exe.parent.mkdir()
            exe.touch()
            cfg = QmtConfig(
                exe_path=str(exe),
                userdata_path=str(userdata),
            )
            loc = locate_qmt(cfg)
            self.assertEqual(loc.userdata_dir, userdata)
            self.assertEqual(loc.install_dir, base)
            self.assertEqual(loc.exe_path, exe)

    def test_discover_fallback_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            userdata = base / "userdata"
            userdata.mkdir()
            exe = base / "bin" / "XtItClient.exe"
            exe.parent.mkdir()
            exe.touch()
            cfg = QmtConfig(userdata_path=str(userdata))
            loc = locate_qmt(cfg)
            self.assertEqual(loc.exe_path, exe)

    def test_empty_locations_for_bogus_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "not_exists" / "userdata_mini"
            cfg = QmtConfig(userdata_path=str(bogus))
            loc = locate_qmt(cfg, allow_scan=False)
            self.assertIsNone(loc.userdata_dir)
            self.assertIsNone(loc.install_dir)
            self.assertIsNone(loc.exe_path)


if __name__ == "__main__":
    unittest.main()
