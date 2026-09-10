import asyncio
import unittest

from bigqmt.mcp.auth import BearerAuthMiddleware


class _Recorder:
    """最小 ASGI 应用，记录是否被调用。"""

    def __init__(self):
        self.called = 0

    async def __call__(self, scope, receive, send):
        self.called += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(middleware, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(scope, receive, send))
    return messages


def _scope(headers=(), path="/mcp", type_="http"):
    return {
        "type": type_,
        "path": path,
        "method": "POST",
        "headers": [(k.lower(), v) for k, v in headers],
    }


class BearerAuthMiddlewareTest(unittest.TestCase):
    def test_requires_non_empty_token(self):
        with self.assertRaises(ValueError):
            BearerAuthMiddleware(_Recorder(), "")

    def test_missing_authorization_is_rejected(self):
        app = _Recorder()
        messages = _run(BearerAuthMiddleware(app, "s3cret"), _scope())
        self.assertEqual(messages[0]["status"], 401)
        self.assertEqual(app.called, 0)

    def test_wrong_token_is_rejected(self):
        app = _Recorder()
        messages = _run(
            BearerAuthMiddleware(app, "s3cret"),
            _scope([(b"authorization", b"Bearer nope")]),
        )
        self.assertEqual(messages[0]["status"], 401)
        self.assertEqual(app.called, 0)

    def test_correct_token_passes_through(self):
        app = _Recorder()
        messages = _run(
            BearerAuthMiddleware(app, "s3cret"),
            _scope([(b"authorization", b"Bearer s3cret")]),
        )
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(app.called, 1)

    def test_health_path_is_open(self):
        app = _Recorder()
        messages = _run(BearerAuthMiddleware(app, "s3cret"), _scope(path="/health"))
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(app.called, 1)

    def test_non_http_scope_passes_through(self):
        app = _Recorder()

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            pass

        asyncio.run(
            BearerAuthMiddleware(app, "s3cret")(
                _scope(type_="lifespan"),
                receive,
                send,
            )
        )
        self.assertEqual(app.called, 1)


if __name__ == "__main__":
    unittest.main()
