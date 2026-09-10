"""纯 ASCII 源码转换工具的测试（含与真实服务脚本的等价性验证）。"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ascii_safe_source import (
    SourceCompatibilityError,
    assert_python36_fstring_safe,
    convert_file,
    convert_text,
    python36_fstring_issues,
    to_ascii,
    with_coding_declaration,
)

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


class EncodingModeTest(unittest.TestCase):
    SOURCE = "# -*- coding: utf-8 -*-\nX = '中文值'\n"

    def test_declaration_is_rewritten(self):
        self.assertTrue(
            with_coding_declaration(self.SOURCE, "gbk").startswith("# -*- coding: gbk -*-")
        )
        self.assertEqual(with_coding_declaration("X = 1\n", "gbk"), "X = 1\n")

    def test_declaration_keeps_crlf(self):
        text = "# -*- coding: utf-8 -*-\r\nX = 1\r\n"
        converted = with_coding_declaration(text, "gbk")
        self.assertTrue(converted.startswith("# -*- coding: gbk -*-\r\n"))

    def test_ascii_mode_is_transcode_immune(self):
        out = convert_text(self.SOURCE, "ascii")
        self.assertTrue(out.isascii())
        # 纯 ASCII 在 GBK 与 UTF-8 下解码结果完全一致
        data = out.encode("ascii")
        self.assertEqual(data.decode("gbk"), out)
        self.assertEqual(data.decode("utf-8"), out)
        scope = {}
        exec(out, scope)  # noqa: S102 - 测试用受控源码
        self.assertEqual(scope["X"], "中文值")

    def test_gbk_mode_keeps_chinese_readable(self):
        out = convert_text(self.SOURCE, "gbk")
        self.assertIn("coding: gbk", out.splitlines()[0])
        data = out.encode("gbk")
        self.assertEqual(data.decode("gbk"), out)
        self.assertIn("'中文值'", out)

    def test_gbk_roundtrip_exec_preserves_value(self):
        out = convert_text(self.SOURCE, "gbk")
        scope = {}
        exec(out.encode("gbk").decode("gbk"), scope)  # noqa: S102 - 测试用受控源码
        self.assertEqual(scope["X"], "中文值")

    def test_gbk_unrepresentable_char_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.py"
            src.write_text("# -*- coding: utf-8 -*-\nX = '\U0001f642'\n", encoding="utf-8")
            with self.assertRaises(UnicodeEncodeError) as ctx:
                convert_file(src, Path(tmp) / "b.py", "gbk")
            self.assertIn("ascii", str(ctx.exception))

    def test_utf8_mode_writes_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.py"
            src.write_text(self.SOURCE, encoding="utf-8")
            dst = Path(tmp) / "b.py"
            convert_file(src, dst, "utf-8")
            raw = dst.read_bytes()
            self.assertEqual(raw.decode("utf-8"), self.SOURCE)


class FStringCompatTest(unittest.TestCase):
    """Python 3.6 的 f-string 限制（PEP 701 之前）。"""

    def test_backslash_in_expression_is_detected(self):
        source = to_ascii('logger.info(f"{name or \'未配置\'}")\n')
        issues = python36_fstring_issues(source)
        self.assertTrue(issues, "应检测出表达式内的反斜杠")
        self.assertIn("反斜杠", issues[0][1])

    def test_assert_raises_with_actionable_message(self):
        source = to_ascii('logger.info(f"{name or \'未配置\'}")\n')
        with self.assertRaises(SourceCompatibilityError) as ctx:
            assert_python36_fstring_safe(source)
        self.assertIn("提取成变量", str(ctx.exception))

    def test_chinese_in_literal_part_is_fine(self):
        source = to_ascii('logger.info(f"值: {value}")\n')
        self.assertTrue(source.isascii())
        self.assertEqual(python36_fstring_issues(source), [])

    def test_same_quote_nesting_is_detected(self):
        source = 'x = f"{d["k"]}"\n'
        issues = python36_fstring_issues(source)
        self.assertTrue(issues)
        self.assertIn("引号", issues[0][1])

    def test_clean_source_has_no_issues(self):
        source = 'x = 1\ny = f"{x}"\n'
        self.assertEqual(python36_fstring_issues(source), [])

    def test_convert_text_rejects_unsafe_source(self):
        with self.assertRaises(SourceCompatibilityError):
            convert_text(
                "# -*- coding: utf-8 -*-\nlogger.info(f\"{n or '未配置'}\")\n",
                "ascii",
            )


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
        gbk_path = Path(cls.tmp.name) / "http_gbk.py"
        convert_file(SERVICE_FILE, gbk_path, "gbk")
        cls.gbk_version = _load(gbk_path, "svc_gbk")
        cls.raw_gbk = gbk_path.read_bytes()

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

    def test_no_python36_fstring_issues(self):
        self.assertEqual(
            python36_fstring_issues(self.raw_ascii.decode("ascii")), []
        )
        self.assertEqual(python36_fstring_issues(self.raw_gbk.decode("gbk")), [])

    def test_gbk_version_is_gbk_and_readable(self):
        # 真 GBK：按 gbk 解码成功，且中文不是乱码
        text = self.raw_gbk.decode("gbk")
        self.assertIn("# -*- coding: gbk -*-", text.splitlines()[0])
        self.assertIn("你的QMT账号", text)

    def test_gbk_version_behaves_identically(self):
        for value in ("8887920826", "", "你的QMT账号", "abc"):
            self.assertEqual(
                self.original.check_account_id(value),
                self.gbk_version.check_account_id(value),
                value,
            )
        self.assertEqual(
            self.original.account_status(""), self.gbk_version.account_status("")
        )
        self.assertEqual(
            {route[0] for route in self.original.make_app().routes},
            {route[0] for route in self.gbk_version.make_app().routes},
        )

    def test_gbk_version_parses_as_python36(self):
        import ast

        ast.parse(self.raw_gbk.decode("gbk"), feature_version=(3, 6))


if __name__ == "__main__":
    unittest.main()
