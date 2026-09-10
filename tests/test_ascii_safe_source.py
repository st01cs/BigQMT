"""纯 ASCII 源码转换工具的测试（含与真实服务脚本的等价性验证）。"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ascii_safe_source import convert_file, to_ascii

SERVICE_FILE = Path(__file__).resolve().parents[1] / "bigqmt" / "service" / "http.py"


class ToAsciiTest(unittest.TestCase):
    def test_non_ascii_becomes_escape(self):
        self.assertEqual(to_ascii("你好"), "\\u4f60\\u597d")

    def test_ascii_is_unchanged(self):
        self.assertEqual(to_ascii("print('ok')\n"), "print('ok')\n")

    def test_output_is_ascii(self):
        converted = to_ascii("# 中文注释\nX = '账号'\n")
        self.assertTrue(converted.isascii())

    def test_string_semantics_preserved(self):
        source = "S = '你的QMT账号'\n"
        scope = {}
        exec(to_ascii(source), scope)  # noqa: S102 - 测试用受控源码
        self.assertEqual(scope["S"], "你的QMT账号")

    def test_docstring_semantics_preserved(self):
        source = 'def f():\n    """中文说明"""\n    return 1\n'
        scope = {}
        exec(to_ascii(source), scope)  # noqa: S102 - 测试用受控源码
        self.assertEqual(scope["f"].__doc__, "中文说明")

    def test_fstring_semantics_preserved(self):
        source = "name = '账号'\nout = f'值={name}'\n"
        scope = {}
        exec(to_ascii(source), scope)  # noqa: S102 - 测试用受控源码
        self.assertEqual(scope["out"], "值=账号")

    def test_comment_escapes_keep_line_structure(self):
        converted = to_ascii("# 注释\nX = 1\n")
        self.assertEqual(len(converted.splitlines()), 2)


class ConvertFileTest(unittest.TestCase):
    def test_writes_ascii_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.py"
            src.write_text("# 中文\nV = '值'\n", encoding="utf-8")
            dst = Path(tmp) / "out" / "a_ascii.py"
            convert_file(src, dst)
            raw = dst.read_bytes()
            self.assertTrue(all(b < 128 for b in raw))
            scope = {}
            exec(raw.decode("ascii"), scope)  # noqa: S102 - 测试用受控源码
            self.assertEqual(scope["V"], "值")


def _install_tornado_stub():
    if "tornado.web" in sys.modules:
        return
    import types

    tornado = types.ModuleType("tornado")
    web = types.ModuleType("tornado.web")
    ioloop = types.ModuleType("tornado.ioloop")

    class RequestHandler:
        pass

    class HTTPError(Exception):
        def __init__(self, status_code=500, log_message=None):
            super().__init__(log_message)
            self.status_code = status_code

    class Application:
        def __init__(self, routes=None, **kwargs):
            self.routes = routes or []

    class IOLoop:
        @staticmethod
        def current():
            return IOLoop()

        def start(self):
            pass

    web.Application = Application
    web.RequestHandler = RequestHandler
    web.HTTPError = HTTPError
    ioloop.IOLoop = IOLoop
    tornado.web = web
    tornado.ioloop = ioloop
    sys.modules["tornado"] = tornado
    sys.modules["tornado.web"] = web
    sys.modules["tornado.ioloop"] = ioloop


def _load(path: Path, name: str):
    _install_tornado_stub()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SERVICE_FILE.is_file(), "缺少 bigqmt/service/http.py")
class ServiceEquivalenceTest(unittest.TestCase):
    """ASCII 版与原始服务脚本必须行为一致。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        converted = Path(cls.tmp.name) / "http_ascii.py"
        convert_file(SERVICE_FILE, converted)
        cls.original = _load(SERVICE_FILE, "svc_original")
        cls.ascii_version = _load(converted, "svc_ascii")
        cls.raw_ascii = converted.read_bytes()

    def test_converted_file_is_pure_ascii(self):
        self.assertTrue(all(b < 128 for b in self.raw_ascii))

    def test_constants_match(self):
        for name in ("ACCOUNT_ID", "TOKEN", "PORT", "PLACEHOLDER_ACCOUNT_IDS"):
            self.assertEqual(
                getattr(self.original, name), getattr(self.ascii_version, name), name
            )

    def test_account_helpers_match(self):
        for value in ("8887920826", "", "你的QMT账号", "abc"):
            self.assertEqual(
                self.original.check_account_id(value),
                self.ascii_version.check_account_id(value),
                value,
            )
        self.assertEqual(
            self.original.mask_account("8887920826"),
            self.ascii_version.mask_account("8887920826"),
        )

    def test_chinese_messages_match(self):
        status_original = self.original.account_status("")
        status_ascii = self.ascii_version.account_status("")
        self.assertEqual(status_original, status_ascii)
        self.assertIn("QMT_ACCOUNT_ID", status_ascii["hint"])

    def test_route_table_matches(self):
        routes_original = {route[0] for route in self.original.make_app().routes}
        routes_ascii = {route[0] for route in self.ascii_version.make_app().routes}
        self.assertEqual(routes_original, routes_ascii)
        self.assertIn("/api/sys/account_status", routes_ascii)

    def test_syntax_error_free(self):
        compile(self.raw_ascii.decode("ascii"), "<ascii_safe>", "exec")


if __name__ == "__main__":
    unittest.main()
