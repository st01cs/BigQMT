"""QMT HTTP API 客户端。

对应后端：`bigqmt.service.http`（Tornado，默认 127.0.0.1:10086，需 `X-Token`）。

约定：
- 后端返回 4xx/5xx、响应非 JSON、网络异常，统一抛 `QMTApiError`，
  由 FastMCP 转成 `isError=true`，避免把失败伪装成成功结果；
- 后端多个 handler 用 `xxx.split(',')` 解析代码列表，因此列表参数一律先 join 成逗号字符串
  （见 `as_csv`）；
- `requests` 懒加载，未安装时仍可导入本模块（便于纯逻辑测试）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from bigqmt.mcp.config import McpConfig, load_mcp_config

logger = logging.getLogger("bigqmt.mcp.client")


class QMTApiError(RuntimeError):
    """QMT HTTP API 调用失败。"""

    def __init__(
        self,
        message: str,
        *,
        method: Optional[str] = None,
        path: Optional[str] = None,
        status_code: Optional[int] = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.status_code = status_code
        self.payload = payload

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"error": str(self)}
        if self.method:
            data["method"] = self.method
        if self.path:
            data["path"] = self.path
        if self.status_code is not None:
            data["status_code"] = self.status_code
        return data


def as_csv(value: Any) -> str:
    """把代码列表规整成后端要求的逗号字符串。

    后端 handler 普遍执行 `stocks.split(',')`，传 list 会直接 500。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def describe_request_error(exc: BaseException) -> str:
    """把 requests 的冗长异常压成一句人话（原始异常仍挂在 __cause__）。"""
    text = str(exc)
    lowered = text.lower()
    if "read timed out" in lowered or "read timeout" in lowered:
        return "读取超时（QMT 后端响应过慢或调用被阻塞）"
    if (
        "connecttimeout" in lowered
        or "connection refused" in lowered
        or "max retries exceeded" in lowered
        or "connection aborted" in lowered
        or "timed out" in lowered
    ):
        return "无法连接（QMT 后端未运行或不可达）"
    return " ".join(text.split())[:120]


class QMTClient:
    """QMT HTTP 客户端。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Any = None,
        config: Optional[McpConfig] = None,
    ) -> None:
        cfg = config or load_mcp_config()
        self.base = (base_url or cfg.qmt_base_url).rstrip("/")
        self.token = token if token is not None else cfg.qmt_token
        self.timeout = timeout if timeout is not None else cfg.request_timeout
        self._session = session

    @property
    def session(self) -> Any:
        """惰性创建 requests.Session（未安装 requests 时在此处报错）。"""
        if self._session is None:
            import requests  # 局部导入：未安装时不影响模块导入

            session = requests.Session()
            session.headers.update(
                {
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Token": self.token,
                }
            )
            self._session = session
        return self._session

    @session.setter
    def session(self, value: Any) -> None:
        self._session = value

    def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        """统一请求方法；失败抛 `QMTApiError`。"""
        url = f"{self.base}{path}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except Exception as exc:  # 网络层异常（requests 未安装/连接失败等）
            raise QMTApiError(
                f"{method} {path} 请求失败：{describe_request_error(exc)}（后端 {self.base}）",
                method=method,
                path=path,
            ) from exc

        status = getattr(resp, "status_code", None)
        text = getattr(resp, "text", "")
        if status is not None and status >= 400:
            raise QMTApiError(
                f"{method} {path} 返回 {status}: {text[:200]}",
                method=method,
                path=path,
                status_code=status,
            )
        try:
            return resp.json()
        except Exception as exc:
            raise QMTApiError(
                f"{method} {path} 响应不是合法 JSON: {text[:200]}",
                method=method,
                path=path,
                status_code=status,
            ) from exc

    # ---- 账户 ----
    def get_holding(self, account: str = "stock") -> Dict:
        return self._req("POST", "/api/holding", json={"account": account})

    def get_total_money(self, account: str = "stock") -> Dict:
        return self._req("POST", "/api/money/total", json={"account": account})

    def get_available_money(self, account: str = "stock") -> Dict:
        return self._req("POST", "/api/money/available", json={"account": account})

    # ---- 行情 ----
    def get_full_tick(self, stocks: Sequence[str] | str) -> Dict:
        """实时分笔行情；后端按逗号字符串解析，故此处统一 join。"""
        return self._req("POST", "/api/data/full_tick", json={"stocks": as_csv(stocks)})

    def get_market_data_ex(
        self,
        stock_code: Sequence[str] | str,
        period: str = "follow",
        fields: str = "",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: str = "follow",
    ) -> Dict:
        """扩展行情；后端读取 `stock_code`（逗号字符串）。"""
        return self._req(
            "POST",
            "/api/data/market_data_ex",
            json={
                "stock_code": as_csv(stock_code),
                "period": period,
                "fields": fields,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": dividend_type,
            },
        )

    def get_market_data(
        self,
        stock_code: Sequence[str] | str,
        fields: Sequence[str] | str,
        start_time: str = "",
        end_time: str = "",
        period: str = "1d",
        dividend_type: str = "none",
        count: int = -1,
    ) -> Dict:
        """行情数据；后端读取 `stock_code` 与 `fields`（均为逗号字符串）。"""
        return self._req(
            "POST",
            "/api/data/market_data",
            json={
                "stock_code": as_csv(stock_code),
                "fields": as_csv(fields),
                "start_time": start_time,
                "end_time": end_time,
                "period": period,
                "dividend_type": dividend_type,
                "count": count,
            },
        )

    def get_trading_dates(
        self,
        market: str = "SH",
        start_date: str = "",
        end_date: str = "",
        count: int = -1,
        period: str = "1d",
    ) -> Dict:
        """交易日列表；后端把该形参直接透传给 `get_trading_dates(market, ...)`。"""
        return self._req(
            "POST",
            "/api/data/trading_dates",
            json={
                "stockcode": market,
                "start_date": start_date,
                "end_date": end_date,
                "count": count,
                "period": period,
            },
        )

    # ---- 交易 ----
    def buy_stock(
        self, stock: str, price: float, volume: int, pr_type: int = 11
    ) -> Dict:
        return self._req(
            "POST",
            "/api/order/buy",
            json={"stock": stock, "price": price, "volume": volume, "prType": pr_type},
        )

    def sell_stock(
        self, stock: str, price: float, volume: int, pr_type: int = 11
    ) -> Dict:
        return self._req(
            "POST",
            "/api/order/sell",
            json={"stock": stock, "price": price, "volume": volume, "prType": pr_type},
        )

    def get_order_status(self, account: str = "stock") -> Dict:
        return self._req("POST", "/api/order/status", json={"account": account})

    def cancel_all_orders(self, account: str = "stock") -> Dict:
        return self._req("POST", "/api/order/cancel_all", json={"account": account})

    # ---- 系统 ----
    def python_version(self) -> Dict:
        return self._req("GET", "/api/sys/python_version")

    def close(self) -> Dict:
        return self._req("POST", "/api/sys/shutdown")


_qmt_client: Optional[QMTClient] = None


def get_client(config: Optional[McpConfig] = None) -> QMTClient:
    """获取或创建 QMT 客户端（单例模式）。"""
    global _qmt_client
    if _qmt_client is None:
        _qmt_client = QMTClient(config=config)
    return _qmt_client


def set_client(client: Optional[QMTClient]) -> None:
    """注入客户端（测试或自定义配置时使用）。"""
    global _qmt_client
    _qmt_client = client


__all__ = [
    "QMTClient",
    "QMTApiError",
    "as_csv",
    "describe_request_error",
    "get_client",
    "set_client",
]
