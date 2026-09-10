"""QMT 侧 HTTP 服务脚本（部署件）。

`http.py` 是跑在 **QMT 客户端内**的 Tornado API（默认监听 127.0.0.1:10086），
使用 QMT 内置 Python（3.6）解释器，通过 `ContextInfo` 访问行情与交易接口，
并要求请求头 `X-Token` 匹配文件内配置。

它由 QMT 策略机制加载（部署到 QMT 的 python 目录后以策略形式运行），
**不作为 Python 包被导入**，因此这里只提供包标记与说明。

注意：`http.py` 的 `ACCOUNT_ID` 需配置为真实资金账号，否则
`/api/money/total`、`/api/money/available` 会返回 500，持仓/委托返回空。
"""

__all__ = []
