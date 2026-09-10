"""QMT 侧 `bigqmt/service/http.py` 测试用的共享 stub 与加载器。

该脚本正常运行在 QMT 内置 Python 3.6 + tornado 环境里；本机测试时注入最小
tornado stub 后按文件加载，直接验证脚本内的纯逻辑（账号解析、自检、错误响应等）。

两个测试文件共用这里的实现，避免各自装一份 stub 造成行为不一致。
"""

import importlib.util
import os
import sys
import types
from pathlib import Path

SERVICE_FILE = Path(__file__).resolve().parents[1] / "bigqmt" / "service" / "http.py"


class StubHTTPError(Exception):
    """对齐 tornado.web.HTTPError：带 status_code 与 log_message。"""

    def __init__(self, status_code=500, log_message=None):
        super().__init__(log_message)
        self.status_code = status_code
        self.log_message = log_message


class StubApplication:
    def __init__(self, routes=None, **kwargs):
        self.routes = routes or []
        self.ContextInfo = None
        self.accountID = None

    def listen(self, *args, **kwargs):
        pass


class StubRequestHandler:
    pass


class StubIOLoop:
    @staticmethod
    def current():
        return StubIOLoop()

    def start(self):
        pass


def install_tornado_stub():
    """真实 tornado 可用时不干预；否则注入最小 stub（幂等）。"""
    if "tornado.web" in sys.modules:
        return
    try:
        import tornado.ioloop  # noqa: F401
        import tornado.web  # noqa: F401

        return
    except ImportError:
        pass

    tornado = types.ModuleType("tornado")
    web = types.ModuleType("tornado.web")
    ioloop = types.ModuleType("tornado.ioloop")
    web.Application = StubApplication
    web.RequestHandler = StubRequestHandler
    web.HTTPError = StubHTTPError
    ioloop.IOLoop = StubIOLoop
    tornado.web = web
    tornado.ioloop = ioloop
    sys.modules["tornado"] = tornado
    sys.modules["tornado.web"] = web
    sys.modules["tornado.ioloop"] = ioloop


def load_service_module(
    env=None, path=None, module_name="qmt_service_http"
):
    """加载服务脚本模块。

    env：非 None 时在加载期间覆盖 QMT_* 环境变量（加载后还原）。
    path：默认加载仓库里的 bigqmt/service/http.py。
    """
    install_tornado_stub()
    target = Path(path) if path is not None else SERVICE_FILE
    saved = {}
    if env is not None:
        for key in ("QMT_ACCOUNT_ID", "QMT_HTTP_TOKEN", "QMT_HTTP_PORT"):
            saved[key] = os.environ.pop(key, None)
        for key, value in env.items():
            os.environ[key] = value
    try:
        spec = importlib.util.spec_from_file_location(module_name, target)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if env is not None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return module
