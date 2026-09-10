import unittest
from unittest import mock

import bigqmt.core.qmt._auto_login as auto_login_mod
from bigqmt.core.qmt._auto_login import (
    AutoLoginError,
    NativeQmtAutoLogin,
)


# --------------------------------------------------------------------- #
# 假 pywinauto 控件树
# --------------------------------------------------------------------- #
class FakeElement:
    def __init__(self, is_password=False, focused=False):
        self.CurrentIsPassword = is_password
        self.CurrentHasKeyboardFocus = focused


class FakeElementInfo:
    def __init__(self, element):
        self.element = element


class FakeEdit:
    def __init__(self, text="", name="", is_password=None, focused=None):
        self._text = text
        self._name = name
        self.typed = []
        self.clicked = 0
        self.focus_calls = 0
        if is_password is not None or focused is not None:
            self.element_info = FakeElementInfo(
                FakeElement(bool(is_password), bool(focused))
            )

    def class_name(self):
        return "Edit"

    def window_text(self):
        return self._text

    def name(self):
        return self._name

    def click_input(self):
        self.clicked += 1

    def type_keys(self, value):
        self.typed.append(value)

    def set_text(self, value):
        self._text = value

    def set_focus(self):
        self.focus_calls += 1
        info = getattr(self, "element_info", None)
        if info is not None:
            info.element.CurrentHasKeyboardFocus = True


class FakeButton:
    def __init__(self, text, on_click=None):
        self._text = text
        self._on_click = on_click
        self.clicked = 0

    def class_name(self):
        return "Button"

    def window_text(self):
        return self._text

    def click_input(self):
        self.clicked += 1
        if self._on_click:
            self._on_click()


class FakeWindow:
    def __init__(self, title="", children=()):
        self._title = title
        self._children = list(children)

    def window_text(self):
        return self._title

    def children(self):
        return list(self._children)

    def set_focus(self):
        pass

    def type_keys(self, value):
        pass


class _Scenario:
    def __init__(self):
        self.logged = False
        self.started = False
        self.killed = False


scenario = _Scenario()


class FakeApplication:
    def __init__(self, backend="uia"):
        self.backend = backend

    def connect(self, path=None, timeout=None):
        return FakeConnectedApp()

    def start(self, path=None, timeout=None):
        scenario.started = True
        return FakeConnectedApp()


class FakeConnectedApp:
    def _build_window(self):
        if scenario.logged:
            return FakeWindow(
                title="迅投QMT交易端 委托 持仓 资产 行情",
                children=[],
            )
        password_edit = FakeEdit(text="", name="密码框")

        def on_login():
            scenario.logged = True

        login_button = FakeButton(text="登录", on_click=on_login)
        account_edit = FakeEdit(text="12345678")
        return FakeWindow(
            title="QMT 用户登录 请输入密码",
            children=[account_edit, password_edit, login_button],
        )

    def top_window(self):
        return self._build_window()

    def kill(self):
        scenario.killed = True


class _ProcessNotFoundError(Exception):
    pass


def _use_fake_deps():
    patchers = [
        mock.patch.object(
            auto_login_mod, "_import_pywinauto",
            return_value=(FakeApplication, _ProcessNotFoundError),
        ),
        mock.patch.object(auto_login_mod, "_import_xtquant_xtdata", return_value=None),
        mock.patch.object(auto_login_mod, "_import_pyautogui", return_value=None),
        mock.patch.object(auto_login_mod.time, "sleep", lambda _s: None),
    ]
    for p in patchers:
        p.start()
    return patchers


class WindowHeuristicTest(unittest.TestCase):
    def test_has_password_field_true(self):
        win = FakeWindow(children=[FakeEdit(name="密码框")])
        self.assertTrue(NativeQmtAutoLogin.has_password_field(win))

    def test_has_password_field_false(self):
        win = FakeWindow(children=[FakeEdit(text="12345678")])
        self.assertFalse(NativeQmtAutoLogin.has_password_field(win))

    def test_find_password_edit_by_name(self):
        edits = [FakeEdit(text="12345678"), FakeEdit(name="密码框")]
        win = FakeWindow(children=edits)
        self.assertIs(NativeQmtAutoLogin.find_password_edit(win), edits[1])

    def test_find_password_edit_fallback_to_second_edit(self):
        edits = [FakeEdit(text="12345678"), FakeEdit(text="")]
        win = FakeWindow(children=edits)
        self.assertIs(NativeQmtAutoLogin.find_password_edit(win), edits[1])

    def test_find_password_edit_prefers_uia_is_password_flag(self):
        """UIA IsPassword=True 的框优先，哪怕它排在最后且无「密码」文本。"""
        acct = FakeEdit(text="12345678")
        blank = FakeEdit(text="")
        pwd = FakeEdit(text="", is_password=True)
        win = FakeWindow(children=[acct, blank, pwd])
        self.assertIs(NativeQmtAutoLogin.find_password_edit(win), pwd)

    def test_extract_captcha_patterns(self):
        self.assertEqual(
            NativeQmtAutoLogin.extract_captcha("验证码 598hi"), "598hi"
        )
        self.assertEqual(
            NativeQmtAutoLogin.extract_captcha("验证码：123456"), "123456"
        )
        self.assertIsNone(NativeQmtAutoLogin.extract_captcha("没有验证码"))


class IsLoggedInWindowTest(unittest.TestCase):
    def test_login_window_reports_not_logged_in(self):
        patchers = _use_fake_deps()
        try:
            scenario.logged = False
            alogin = NativeQmtAutoLogin(
                exe_path="D:/fake/XtItClient.exe", password="pw"
            )
            self.assertFalse(alogin.is_logged_in())
        finally:
            for p in patchers:
                p.stop()

    def test_logged_in_window_reports_true(self):
        patchers = _use_fake_deps()
        try:
            scenario.logged = True
            alogin = NativeQmtAutoLogin(
                exe_path="D:/fake/XtItClient.exe", password="pw"
            )
            self.assertTrue(alogin.is_logged_in())
        finally:
            for p in patchers:
                p.stop()


class LoginFlowTest(unittest.TestCase):
    def setUp(self):
        scenario.logged = False
        scenario.started = False
        scenario.killed = False

    def test_login_reaches_logged_in(self):
        patchers = _use_fake_deps()
        try:
            alogin = NativeQmtAutoLogin(
                exe_path="D:/fake/XtItClient.exe", password="secret"
            )
            self.assertTrue(alogin.login(timeout=5))
            self.assertTrue(scenario.logged)
        finally:
            for p in patchers:
                p.stop()

    def test_login_without_password_raises(self):
        patchers = _use_fake_deps()
        try:
            alogin = NativeQmtAutoLogin(exe_path="D:/fake/XtItClient.exe")
            with self.assertRaises(AutoLoginError):
                alogin.login(timeout=5)
        finally:
            for p in patchers:
                p.stop()

    def test_login_without_exe_raises(self):
        patchers = _use_fake_deps()
        try:
            alogin = NativeQmtAutoLogin(password="secret")
            with self.assertRaises(AutoLoginError):
                alogin.login(timeout=5)
        finally:
            for p in patchers:
                p.stop()

    def test_missing_pywinauto_raises_dependency_error(self):
        patchers = [
            mock.patch.object(
                auto_login_mod,
                "_import_pywinauto",
                side_effect=auto_login_mod.AutoLoginDependencyError("缺少 pywinauto"),
            ),
            mock.patch.object(auto_login_mod.time, "sleep", lambda _s: None),
        ]
        for p in patchers:
            p.start()
        try:
            alogin = NativeQmtAutoLogin(
                exe_path="D:/fake/XtItClient.exe", password="secret"
            )
            with self.assertRaises(auto_login_mod.AutoLoginDependencyError):
                alogin.login(timeout=5)
        finally:
            for p in patchers:
                p.stop()


class _StubApp:
    def __init__(self, win):
        self._win = win

    def top_window(self):
        return self._win


def _deps_with_sleep_recorder(recorded):
    return [
        mock.patch.object(
            auto_login_mod, "_import_pywinauto",
            return_value=(FakeApplication, _ProcessNotFoundError),
        ),
        mock.patch.object(auto_login_mod, "_import_xtquant_xtdata", return_value=None),
        mock.patch.object(auto_login_mod, "_import_pyautogui", return_value=None),
        mock.patch.object(auto_login_mod.time, "sleep", lambda s: recorded.append(s)),
    ]


class LoginPacingTest(unittest.TestCase):
    """登录动作节奏：先确保焦点，再留停顿后输入。"""

    def _run_once(self, password_edit, recorded, **kwargs):
        patchers = _deps_with_sleep_recorder(recorded)
        for p in patchers:
            p.start()
        try:
            alogin = NativeQmtAutoLogin(
                exe_path="D:/fake/XtItClient.exe", password="secret", **kwargs
            )
            win = FakeWindow(
                title="QMT 登录",
                children=[FakeEdit(text="12345678"), password_edit, FakeButton("登录")],
            )
            alogin._connect = lambda: _StubApp(win)
            alogin._do_login_once()
        finally:
            for p in patchers:
                p.stop()

    def test_focus_forced_when_click_does_not_focus(self):
        recorded = []
        pwd = FakeEdit(name="密码框", focused=False)
        self._run_once(pwd, recorded, action_delay=0.25, type_interval=0.0)
        self.assertGreaterEqual(pwd.focus_calls, 1)
        self.assertEqual(pwd.typed[-1], "secret")

    def test_action_delay_pauses_between_actions(self):
        recorded = []
        pwd = FakeEdit(name="密码框", focused=True)
        self._run_once(pwd, recorded, action_delay=0.25, type_interval=0.0)
        self.assertIn(0.5, recorded)  # 初始等待（mult=2）
        self.assertGreaterEqual(recorded.count(0.25), 3)  # 焦点后/清空后/提交前
        self.assertEqual(pwd.typed[-1], "secret")


if __name__ == "__main__":
    unittest.main()
