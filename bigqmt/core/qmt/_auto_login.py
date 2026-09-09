"""原生 QMT（完整版客户端）GUI 自动登录。

对应 docs/QMT_STARTUP_PLAN.md 第 5 节 _auto_login.py：
- 原生 QMT 客户端 GUI 因券商/版本差异较大：优先用 pywinauto(UIA)
  控件树定位密码框与登录按钮；定位不到时退化为「Tab + 回车」流程
  （参考 EasyXT core/auto_login/qmt_login.py，但去掉 miniQMT 分支）；
- 登录态判定以 xtquant.is_connected() 为准，pywinauto 窗口启发式兜底；
- 自动登录失败 / 未配置密码时保留可见窗口，交由上层进入人工登录流程，
  避免无头盲操作把账户锁死。

依赖：pywinauto（必需，控件操作）；pyautogui（退化路径可选）。
依赖均在调用时才导入，未安装时抛出 AutoLoginDependencyError，
不影响 bigqmt.core.qmt 其余模块的导入。
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# 登录界面特征词（出现任意一个 => 判定为未登录/登录中）
LOGIN_KEYWORDS: Tuple[str, ...] = (
    "登录",
    "密码",
    "账号",
    "用户",
    "验证码",
    "login",
    "password",
    "username",
    "user",
)
# 已登录主界面特征词（出现任意一个 => 判定为已登录）
LOGGED_IN_KEYWORDS: Tuple[str, ...] = (
    "委托",
    "持仓",
    "交易",
    "行情",
    "资产",
    "策略",
    "资金",
    "查询",
)
CAPTCHA_KEYWORDS: Tuple[str, ...] = ("验证码", "captcha")
LOGIN_BUTTON_TEXTS: Tuple[str, ...] = ("登录", "登 录", "确定", "确 定", "OK", "ok")


class AutoLoginError(RuntimeError):
    """自动登录失败。"""


class AutoLoginDependencyError(AutoLoginError):
    """缺少 pywinauto / pyautogui 等依赖。"""


def _import_pywinauto():
    """延迟导入 pywinauto（Application + ProcessNotFoundError）。"""
    try:
        from pywinauto.application import Application, ProcessNotFoundError

        return Application, ProcessNotFoundError
    except ImportError as exc:
        raise AutoLoginDependencyError(
            "缺少依赖 pywinauto，请安装：pip install bigqmt[auto_login]"
        ) from exc


def _import_xtquant_xtdata():
    """延迟导入 xtquant.xtdata；未安装返回 None。"""
    try:
        from xtquant import xtdata

        return xtdata
    except ImportError:
        return None


def _import_pyautogui():
    """延迟导入 pyautogui；未安装返回 None。"""
    try:
        import pyautogui

        return pyautogui
    except ImportError:
        return None


def _text_has_any(text: str, keywords: Tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)


def _control_text(control) -> str:
    """读取控件文本（window_text / name / text，兼容不同 pywinauto 控件）。"""
    for attr in ("window_text", "text", "name"):
        fn = getattr(control, attr, None)
        if fn is None:
            continue
        try:
            value = fn() if callable(fn) else fn
        except Exception:
            continue
        if value:
            return str(value)
    return ""


def _control_class(control) -> str:
    fn = getattr(control, "class_name", None)
    try:
        value = fn() if callable(fn) else fn
        return str(value or "")
    except Exception:
        return ""


def _iter_children(window):
    fn = getattr(window, "children", None)
    if fn is None:
        return
    try:
        for child in fn():
            yield child
    except Exception:
        return


def _iter_edit_controls(window) -> List:
    edits = []
    for child in _iter_children(window):
        if "Edit" in _control_class(child):
            edits.append(child)
    return edits


class NativeQmtAutoLogin:
    """原生 QMT 客户端自动登录（窗口自动化）。"""

    def __init__(
        self,
        exe_path: Optional[str] = None,
        password: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        self.exe_path = exe_path
        self.password = password
        self.data_dir = data_dir
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 登录态判定
    # ------------------------------------------------------------------ #
    @staticmethod
    def _xtdata_connected() -> bool:
        xtdata = _import_xtquant_xtdata()
        if xtdata is None or not hasattr(xtdata, "is_connected"):
            return False
        try:
            return bool(xtdata.is_connected())
        except Exception:
            return False

    def is_running(self) -> bool:
        """客户端进程窗口是否可连接（依赖 pywinauto）。"""
        Application, _ = _import_pywinauto()
        try:
            Application(backend="uia").connect(path=self.exe_path, timeout=2)
            return True
        except Exception:
            return False

    def is_logged_in(self) -> bool:
        """登录态判定：xtquant 优先，窗口启发式兜底。"""
        if self._xtdata_connected():
            return True
        return self._is_logged_in_via_window()

    def _is_logged_in_via_window(self) -> bool:
        try:
            Application, _ = _import_pywinauto()
            app = Application(backend="uia").connect(path=self.exe_path, timeout=2)
            win = app.top_window()
            text = _control_text(win)
            if _text_has_any(text, LOGIN_KEYWORDS):
                return False
            if self.has_password_field(win):
                return False
            if _text_has_any(text, LOGGED_IN_KEYWORDS):
                return True
            # 无法确认登录态时保守判定为未登录，交由人工/重试处理
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 窗口控件定位
    # ------------------------------------------------------------------ #
    @staticmethod
    def has_password_field(window) -> bool:
        """窗口是否存在「密码」输入框（登录界面特征）。"""
        for child in _iter_children(window):
            if "Edit" not in _control_class(child):
                continue
            text = _control_text(child)
            if "密码" in text or "password" in text.lower():
                return True
        return False

    @staticmethod
    def find_password_edit(window):
        """定位密码输入框。

        策略：
        1. 控件文本/名称含「密码/password」的 Edit；
        2. 退化为「第 2 个 Edit」（假定 [账号, 密码, (验证码)] 顺序）；
        3. 仅 1 个 Edit 时取其本身。
        """
        edits = _iter_edit_controls(window)
        if not edits:
            return None
        for edit in edits:
            text = (_control_text(edit) + " " + str(getattr(edit, "friendly_class_name", "") or "")).lower()
            if "密码" in text or "password" in text:
                return edit
        if len(edits) >= 2:
            return edits[1]
        return edits[0]

    @staticmethod
    def find_login_button(window):
        """定位登录/确定按钮。"""
        for child in _iter_children(window):
            if "Button" not in _control_class(child):
                continue
            text = _control_text(child).strip()
            if text in LOGIN_BUTTON_TEXTS:
                return child
        return None

    @staticmethod
    def has_captcha_feature(window) -> bool:
        text = _control_text(window)
        if _text_has_any(text, CAPTCHA_KEYWORDS):
            return True
        return len(_iter_edit_controls(window)) >= 3

    @staticmethod
    def extract_captcha(text: str) -> Optional[str]:
        """从窗口文本中尽力提取验证码（如 598hi / 123456）。"""
        match = re.search(r"(\d{3,5}[a-zA-Z]{2,3})", text)
        if match:
            return match.group(1)
        match = re.search(r"(\d{4,6})", text)
        if match:
            return match.group(1)
        return None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        Application, _ = _import_pywinauto()
        if self.exe_path is None:
            raise AutoLoginError("未配置 QMT 可执行文件（QMT_EXE_PATH）")
        Application(backend="uia").start(self.exe_path, timeout=10)
        time.sleep(5)  # 等待登录窗口加载

    def _ensure_started(self) -> None:
        if not self.is_running():
            self._start()

    def _connect(self):
        Application, _ = _import_pywinauto()
        return Application(backend="uia").connect(path=self.exe_path, timeout=5)

    def _do_login_once(self) -> None:
        """执行一次密码填写 + 提交（含验证码尝试）。"""
        app = self._connect()
        win = app.top_window()
        try:
            win.set_focus()
        except Exception:
            pass
        time.sleep(1)

        pwd_edit = self.find_password_edit(win)
        pyautogui = _import_pyautogui()
        if pwd_edit is not None:
            try:
                pwd_edit.click_input()
                pwd_edit.type_keys("^a")
                pwd_edit.type_keys(self.password)
            except Exception:
                try:
                    pwd_edit.set_text(self.password)
                except Exception:
                    raise AutoLoginError("无法向密码框输入密码")
        elif pyautogui is not None:
            pyautogui.press("tab")  # 从账号框跳到密码框
            pyautogui.typewrite(self.password, interval=0.05)
        else:
            raise AutoLoginError("找不到密码输入框且缺少 pyautogui 退化路径")

        # 提交登录
        button = self.find_login_button(win)
        try:
            if button is not None:
                button.click_input()
            elif pyautogui is not None:
                pyautogui.press("enter")
            else:
                win.type_keys("{ENTER}")
        except Exception:
            if pyautogui is not None:
                pyautogui.press("enter")
            else:
                raise
        time.sleep(3)

        # 验证码尽力处理（数学算式/图片 OCR 不在本里程碑内）
        try:
            if self.has_captcha_feature(win):
                code = self.extract_captcha(_control_text(win))
                if code:
                    if pyautogui is not None:
                        pyautogui.typewrite(code, interval=0.08)
                        pyautogui.press("enter")
                    else:
                        win.type_keys(code)
                        win.type_keys("{ENTER}")
        except Exception as exc:  # pragma: no cover - 验证码处理失败不阻断主流程
            logger.debug("验证码自动处理失败: %s", exc)

    def login(self, restart: bool = False, timeout: int = 60) -> bool:
        """启动并自动登录原生 QMT 客户端。

        Returns:
            bool: 是否已登录。失败时保留窗口，last_error 给出原因。
        """
        if not self.password:
            self.last_error = "未配置 QMT_PASSWORD，无法自动登录（可人工登录）"
            raise AutoLoginError(self.last_error)
        if not self.exe_path:
            self.last_error = "未配置 QMT 可执行文件（QMT_EXE_PATH）"
            raise AutoLoginError(self.last_error)

        deadline = time.monotonic() + max(1, int(timeout))
        try:
            if restart and self.is_running():
                try:
                    app = self._connect()
                    app.kill()
                    time.sleep(3)
                except Exception:
                    pass
            self._ensure_started()

            if self.is_logged_in():
                return True

            while time.monotonic() < deadline:
                if self.is_logged_in():
                    return True
                try:
                    self._do_login_once()
                except AutoLoginDependencyError:
                    raise
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.debug("自动登录步骤异常（继续重试）: %s", exc)
                time.sleep(2)

            if self.is_logged_in():
                return True
            self.last_error = "自动登录超时：可能需要人工处理验证码或服务器选择"
            return False
        except AutoLoginError:
            raise
        except Exception as exc:
            self.last_error = f"自动登录异常: {exc}"
            raise AutoLoginError(self.last_error) from exc


__all__ = [
    "AutoLoginError",
    "AutoLoginDependencyError",
    "NativeQmtAutoLogin",
    "LOGIN_KEYWORDS",
    "LOGGED_IN_KEYWORDS",
]
