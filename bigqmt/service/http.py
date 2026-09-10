# -*- coding: utf-8 -*-
# author公众号：可转债量化分析
import json
import locale
import math
import os
from tornado.web import Application, RequestHandler, HTTPError
from tornado.ioloop import IOLoop
import logging

# 自定义
# 注意：本脚本运行在 QMT 内置 Python 3.6 中，环境变量需在
# QMT 策略环境/系统环境变量中配置后重启策略生效。
PLACEHOLDER_ACCOUNT_IDS = ('你的QMT账号', 'your_account_id', 'your_qmt_account')


def resolve_account_id(env=None):
    """解析资金账号：取环境变量 QMT_ACCOUNT_ID（占位符视为未配置）。"""
    source = os.environ if env is None else env
    raw = (source.get('QMT_ACCOUNT_ID') or '').strip()
    if raw.lower() in [item.lower() for item in PLACEHOLDER_ACCOUNT_IDS]:
        return ''
    return raw


def check_account_id(account_id):
    """账号格式自检，返回 (ok, message)。"""
    if not account_id:
        return False, "未配置资金账号（环境变量 QMT_ACCOUNT_ID 为空）"
    text = str(account_id).strip()
    if text.lower() in [item.lower() for item in PLACEHOLDER_ACCOUNT_IDS]:
        return False, "资金账号仍是占位符，请填写真实资金账号"
    if not text.isdigit():
        return False, "资金账号应为纯数字，请检查是否误填了其他内容"
    return True, "格式正确"


def mask_account(account_id):
    """日志/接口中脱敏展示账号。"""
    text = str(account_id or '')
    if not text:
        return ''
    if len(text) <= 4:
        return '*' * len(text)
    return '*' * (len(text) - 4) + text[-4:]


def resolve_token(env=None):
    """解析鉴权 token：QMT_HTTP_TOKEN，缺省回退到历史默认值。"""
    source = os.environ if env is None else env
    return (source.get('QMT_HTTP_TOKEN') or '').strip() or "123456789"


def resolve_port(env=None):
    """解析监听端口：QMT_HTTP_PORT，缺省 10086。"""
    source = os.environ if env is None else env
    raw = (source.get('QMT_HTTP_PORT') or '').strip()
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 10086
    return port if 0 < port < 65536 else 10086


ACCOUNT_ID = resolve_account_id()
TOKEN = resolve_token()
PORT = resolve_port()


# ===================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
try:  # 非中文系统可能没有 'chinese' locale，不应阻断服务启动
    locale.setlocale(locale.LC_CTYPE, 'chinese')
except locale.Error:
    pass


def safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(f"{func.__name__} 调用失败: {e}")
        return None


def _trade_detail(*args):
    """调用 QMT 内置 get_trade_detail_data（非 QMT 环境下返回 None）。"""
    func = globals().get('get_trade_detail_data')
    if func is None:
        return None
    return safe_call(func, *args)


def account_status(account_id, probe=True):
    """账号自检：配置是否合法 + 能否取到资金数据。

    返回 dict，可直接作为 /api/sys/account_status 的响应体。
    """
    ok, message = check_account_id(account_id)
    status = {
        "account_id": mask_account(account_id),
        "configured": bool(account_id),
        "reachable": False,
        "message": message,
    }
    if not ok:
        status["hint"] = "在 QMT 策略/系统环境变量中设置 QMT_ACCOUNT_ID 后重启本策略"
        return status
    if not probe:
        return status
    data = _trade_detail(account_id, 'stock', 'account')
    info = data[0] if data else None
    if info is None:
        status["message"] = "账号已配置但取不到资金数据（未登录 / 未绑定该账号 / 账号类型不匹配）"
        status["hint"] = "确认 QMT 已登录该资金账号，且策略绑定的账号一致"
        return status
    status["reachable"] = True
    status["message"] = "账号可用"
    status["total_money"] = round(getattr(info, 'm_dBalance', 0.0) or 0.0, 2)
    status["available_money"] = round(getattr(info, 'm_dAvailable', 0.0) or 0.0, 2)
    return status


def require_account(handler):
    """账号未配置时直接返回 503（附可执行的修复指引），返回 True 表示已中断。"""
    if handler.acc():
        return False
    raise HTTPError(
        503,
        "资金账号未配置：请在 QMT 策略/系统环境变量中设置 QMT_ACCOUNT_ID 后重启本策略"
    )


def normalize_date8(value, default):
    """把日期规整成 QMT 需要的 YYYYMMDD；空值或格式不对时用 default。

    `ContextInfo.get_turnover_rate` 等方法要求 8 位日期，传空串会直接返回空结果。
    """
    text = str(value or '').strip()
    if not text:
        return default
    compact = text.replace('-', '').replace('/', '').replace('.', '')
    if len(compact) == 8 and compact.isdigit():
        return compact
    return default


def is_empty_result(value):
    """判断 QMT 返回值是否为空（兼容 DataFrame/ndarray 与 dict/list）。"""
    if value is None:
        return True
    try:
        return len(value) == 0
    except TypeError:
        return False


def is_port_in_use(port, host='127.0.0.1', timeout=0.5):
    """检测端口是否已被占用（QMT 停止策略后套接字可能仍被占用）。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        return sock.connect_ex((host, int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def sanitize_json(value):
    """把 NaN / Infinity 递归转成 None。

    `json.dumps` 默认会输出裸 `NaN`/`Infinity`，这不是合法 JSON，
    严格解析的 MCP 客户端会直接报错；QMT 侧不少接口在无数据时返回 NaN。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return dict((key, sanitize_json(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


#: `/api/data/query` 允许调用的只读方法白名单。
#: 只收录「查询/读取」类方法；任何下单、撤单、任务控制、参数设置类方法都不在此列。
#: 另注意：`get_scale_and_rank` / `get_scale_and_stock` 实测会阻塞并拖死策略线程
#: （native 层崩溃，无 Python traceback），故不收录。
READONLY_CTX_METHODS = (
    'get_close_price', 'get_last_close', 'get_market_data_ex_ori', 'subscribe_whole_quote',
    'get_finance', 'get_raw_financial_data', 'get_float_caps', 'get_holder_num',
    'get_smallcap', 'get_midcap', 'get_largecap',
    'is_stock', 'is_future', 'is_fund', 'get_stock_type', 'get_ETF_list',
    'get_option_undl', 'stockcode_in_rzrk',
    'get_net_value', 'get_product_asset_value', 'get_product_share',
    'get_product_init_share',
    'get_back_test_index', 'get_commission', 'get_slippage',
    'load_stk_list', 'load_stk_vol_list', 'get_turn_over_rate',
)


def log_startup_self_check():
    """启动自检：账号配置 + 交易账号连通性。只写日志，不阻断服务启动。"""
    ok, message = check_account_id(ACCOUNT_ID)
    account_text = mask_account(ACCOUNT_ID) or "(未配置)"
    logger.info("=" * 60)
    logger.info("QMT HTTP API 启动自检")
    logger.info(f"  监听端口   : {PORT}（鉴权头 X-Token: {'*' * len(TOKEN)}）")
    logger.info(f"  资金账号   : {account_text}")
    if not ok:
        logger.error(f"  [FAIL] {message}")
        logger.error(
            "         修复：在 QMT 策略/系统环境变量中设置 QMT_ACCOUNT_ID 后重启策略；"
            "未配置时资金/持仓/委托接口将返回 503。"
        )
    else:
        logger.info("  [OK] 账号格式校验通过")
        data = _trade_detail(ACCOUNT_ID, 'stock', 'account')
        info = data[0] if data else None
        if info is None:
            logger.warning(
                "  [WARN] 暂未取到资金数据（账号未登录/未绑定，或策略启动早于交易连接）"
            )
        else:
            logger.info(f"  [OK] 资金账号连通，总资产={getattr(info, 'm_dBalance', 0.0)}")
    logger.info("=" * 60)


# ============= BaseHandler =============

AUTH_EXEMPT = set()


def no_auth(cls):
    AUTH_EXEMPT.add(cls)
    return cls


class BaseHandler(RequestHandler):
    def prepare(self):
        if self.__class__ not in AUTH_EXEMPT:
            token = self.request.headers.get('X-Token')
            if token != TOKEN:
                raise HTTPError(401, "认证失败：token 无效或缺失")

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json; charset=utf-8")

    def write_error(self, status_code, **kwargs):
        # 优先使用 raise HTTPError(...) 时传入的 log_message，
        # 否则客户端只会看到 Tornado 的默认 reason（如 "Service Unavailable"）
        message = self._reason
        exc_info = kwargs.get('exc_info')
        if exc_info:
            exc = exc_info[1]
            log_message = getattr(exc, 'log_message', None)
            if log_message:
                message = log_message
            elif isinstance(exc, NameError):
                # 后端脚本调用了当前 QMT 环境未注入的全局函数
                message = f"QMT 接口在当前环境不可用（{exc}）"
        self.finish(json.dumps({
            "error": message,
            "status_code": status_code
        }, ensure_ascii=False))

    def ctx(self):
        return self.application.ContextInfo

    def acc(self):
        return self.application.accountID


# ============= 1. ContextInfo 属性 =============
# ContextInfo.period - 获取当前周期
class ContextPeriodHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"period": self.ctx().period}, ensure_ascii=False))

# ContextInfo.barpos - 获取当前K线索引号
class ContextBarposHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"barpos": self.ctx().barpos}, ensure_ascii=False))

# ContextInfo.time_tick_size - 获取当前K线数目
class ContextTimeTickSizeHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"time_tick_size": self.ctx().time_tick_size}, ensure_ascii=False))

# ContextInfo.stockcode - 获取当前主图品种代码
class ContextStockCodeHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"stockcode": self.ctx().stockcode}, ensure_ascii=False))

# ContextInfo.dividend_type - 获取当前复权方式
class ContextDividendTypeHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"dividend_type": self.ctx().dividend_type}, ensure_ascii=False))

# ContextInfo.market - 获取当前主图市场
class ContextMarketHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"market": self.ctx().market}, ensure_ascii=False))

# ContextInfo.do_back_test - 是否开启回测模式
class ContextDoBackTestHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"do_back_test": self.ctx().do_back_test}, ensure_ascii=False))

# ContextInfo.benchmark - 获取回测基准
class ContextBenchmarkHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"benchmark": self.ctx().benchmark}, ensure_ascii=False))

# ContextInfo.capital - 获取回测初始资金
class ContextCapitalHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"capital": self.ctx().capital}, ensure_ascii=False))

# ContextInfo.get_universe() - 获取股票池中的股票
class ContextUniverseHandler(BaseHandler):
    def get(self):
        self.write(json.dumps({"universe": self.ctx().get_universe()}, ensure_ascii=False))


# ============= 2. 数据查询 (ContextInfo get_*) =============
# ContextInfo.get_stock_name() - 根据代码获取股票名称
class StockNameHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_stock_name, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "name": ret}, ensure_ascii=False))

# get_open_date() - 根据代码获取上市时间
class OpenDateHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_open_date, stockcode)
        if ret is None:  # 兜底：合约详情里也有上市日期
            detail = safe_call(self.ctx().get_instrument_detail, stockcode) or {}
            ret = detail.get('OpenDate')
        self.write(json.dumps({"stockcode": stockcode, "open_date": ret}, ensure_ascii=False))

# ContextInfo.get_last_volume() - 获取最新流通股本
class LastVolumeHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_last_volume, stockcode)
        if ret is None:
            raise HTTPError(500, "获取流通股本失败")
        self.write(json.dumps({"stockcode": stockcode, "last_volume": ret}, ensure_ascii=False))

# ContextInfo.get_bar_timetag() - 获取K线时间戳
class BarTimetagHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        index = int(data.get('index', -1))
        ret = safe_call(self.ctx().get_bar_timetag, index)
        self.write(json.dumps({"index": index, "timetag": ret}, ensure_ascii=False))

# ContextInfo.get_tick_timetag() - 获取最新分笔时间戳
class TickTimetagHandler(BaseHandler):
    def get(self):
        ret = safe_call(self.ctx().get_tick_timetag)
        self.write(json.dumps({"timetag": ret}, ensure_ascii=False))

# ContextInfo.get_sector() - 获取指数成份股
class SectorHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        sector = data.get('sector', '')
        realtime = data.get('realtime', '0')
        if not sector:
            raise HTTPError(400, "need args sector")
        ret = safe_call(self.ctx().get_sector, sector, int(realtime) if realtime != '0' else 0)
        self.write(json.dumps({"sector": sector, "stocks": ret or []}, ensure_ascii=False))

# ContextInfo.get_industry() - 获取行业成份股
class IndustryHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        industry = data.get('industry', '')
        if not industry:
            raise HTTPError(400, "need args industry")
        print(industry)
        ret = safe_call(self.ctx().get_industry, industry)
        self.write(json.dumps({"industry": industry, "stocks": ret or []}, ensure_ascii=False))

# ContextInfo.get_stock_list_in_sector() - 获取板块成份股
class StockListInSectorHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        sectorname = data.get('sectorname', '')
        if not sectorname:
            raise HTTPError(400, "need args sectorname")
        ret = safe_call(self.ctx().get_stock_list_in_sector, sectorname)
        self.write(json.dumps({"sectorname": sectorname, "stocks": ret or []}, ensure_ascii=False))

# ContextInfo.get_weight_in_index() - 获取指数中权重
class WeightInIndexHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        indexcode = data.get('indexcode', '')
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_weight_in_index, indexcode, stockcode)
        self.write(json.dumps({"indexcode": indexcode, "stockcode": stockcode, "weight": ret}, ensure_ascii=False))

# ContextInfo.get_contract_multiplier() - 获取合约乘数
class ContractMultiplierHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        contractcode = data.get('contractcode', '')
        ret = safe_call(self.ctx().get_contract_multiplier, contractcode)
        self.write(json.dumps({"contractcode": contractcode, "multiplier": ret}, ensure_ascii=False))

# ContextInfo.get_risk_free_rate() - 获取无风险利率
class RiskFreeRateHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        index = int(data.get('index', '-1'))
        ret = safe_call(self.ctx().get_risk_free_rate, index)
        self.write(json.dumps({"index": index, "risk_free_rate": ret}, ensure_ascii=False))

# ContextInfo.get_date_location() - 获取日期对应的K线索引
class DateLocationHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        strdate = data.get('strdate', '')
        ret = safe_call(self.ctx().get_date_location, strdate)
        self.write(json.dumps({"strdate": strdate, "location": ret}, ensure_ascii=False))

# ContextInfo.get_history_data() - 获取历史行情数据(多品种字典)
class HistoryDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        length = int(data.get('len', '10'))
        period = data.get('period', '1d')
        field = data.get('field', 'close')
        dividend_type = int(data.get('dividend_type', '0'))
        skip_paused = data.get('skip_paused', 'true').lower() == 'true'
        ret = safe_call(self.ctx().get_history_data, length, period, field, dividend_type, skip_paused)
        self.write(json.dumps({"data": ret} if ret else {"error": "获取历史数据失败"}, ensure_ascii=False))

# ContextInfo.get_market_data() - 获取行情数据(DataFrame)
class MarketDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        fields = data.get('fields', '')
        stock_code = data.get('stock_code', '')
        start_time = data.get('start_time', '')
        end_time = data.get('end_time', '')
        period = data.get('period', '1d')
        dividend_type = data.get('dividend_type', 'none')
        count = int(data.get('count', '-1'))
        fields_list = [f.strip() for f in fields.split(',')] if fields else []
        stock_list = [s.strip() for s in stock_code.split(',')] if stock_code else []
        ret = safe_call(self.ctx().get_market_data, fields_list, stock_list, start_time, end_time, True, period, dividend_type, count)
        if ret is None:
            raise HTTPError(500, "获取行情数据失败")
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret}, ensure_ascii=False, default=str))

# ContextInfo.get_market_data_ex() - 获取扩展行情(Level2)
class MarketDataExHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        fields = data.get('fields', '')
        stock_code = data.get('stock_code', '')
        period = data.get('period', 'follow')
        start_time = data.get('start_time', '')
        end_time = data.get('end_time', '')
        count = int(data.get('count', '-1'))
        dividend_type = data.get('dividend_type', 'follow')
        fields_list = [f.strip() for f in fields.split(',')] if fields else []
        stock_list = [s.strip() for s in stock_code.split(',')] if stock_code else []
        ret = safe_call(self.ctx().get_market_data_ex, fields_list, stock_list, period, start_time, end_time, count, dividend_type)
        if ret is None:
            raise HTTPError(500, "获取扩展行情失败")
        result = {}
        for k, v in ret.items():
            if hasattr(v, 'to_dict'):
                result[k] = v.to_dict()
            else:
                result[k] = str(v)
        self.write(json.dumps({"data": result}, ensure_ascii=False, default=str))

# ContextInfo.get_full_tick() - 获取分笔数据
class FullTickHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stocks = data.get('stocks', '')
        if not stocks:
            raise HTTPError(400, "need args stocks")
        code_list = [s.strip() for s in stocks.split(',')]
        ret = safe_call(self.ctx().get_full_tick, code_list)
        if not ret:
            raise HTTPError(500, "获取分笔行情失败")
        self.write(json.dumps(ret, ensure_ascii=False, default=str))

# ContextInfo.get_divid_factors() - 获取除权除息和复权因子
class DividFactorsHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_divid_factors, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "factors": ret or {}}, ensure_ascii=False))

# ContextInfo.get_main_contract() - 获取期货主力合约
class MainContractHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        codemarket = data.get('codemarket', '')
        ret = safe_call(self.ctx().get_main_contract, codemarket)
        self.write(json.dumps({"codemarket": codemarket, "main_contract": ret}, ensure_ascii=False))

# timetag_to_datetime() - 毫秒时间戳转日期时间
class TimetagToDatetimeHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        timetag = int(data.get('timetag', '0'))
        fmt = data.get('format', '%Y-%m-%d %H:%M:%S')
        ret = safe_call(timetag_to_datetime, timetag, fmt)
        self.write(json.dumps({"timetag": timetag, "datetime": ret}, ensure_ascii=False))

# ContextInfo.get_total_share() - 获取总股本
class TotalShareHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_total_share, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "total_share": ret}, ensure_ascii=False))

# ContextInfo.get_trading_dates() - 获取交易日列表
class TradingDatesHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        start_date = data.get('start_date', '')
        end_date = data.get('end_date', '')
        count = data.get('count', '')
        period = data.get('period', '1d')
        count_int = int(count) if count else -1
        ret = safe_call(self.ctx().get_trading_dates, stockcode, start_date, end_date, count_int, period)
        self.write(json.dumps({"dates": ret or []}, ensure_ascii=False))

# ContextInfo.get_svol() - 获取内盘成交量
class SvolHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_svol, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "svol": ret}, ensure_ascii=False))

# ContextInfo.get_bvol() - 获取外盘成交量
class BvolHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_bvol, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "bvol": ret}, ensure_ascii=False))

# ContextInfo.get_longhubang() - 获取龙虎榜数据
class LonghubangHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_list = data.get('stock_list', '')
        startTime = data.get('startTime', '')
        endTime = data.get('endTime', '')
        slist = [s.strip() for s in stock_list.split(',')] if stock_list else []
        ret = safe_call(self.ctx().get_longhubang, slist, startTime, endTime)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret} if ret else {"error": "获取龙虎榜数据失败"}, ensure_ascii=False, default=str))

# ContextInfo.get_north_finance_change() - 北向资金变化（市场级每日流入/流出）
class NorthFinanceChangeHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        period = data.get('period', '1d')
        ret = safe_call(self.ctx().get_north_finance_change, period)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        if is_empty_result(ret):
            raise HTTPError(503, "获取北向资金变化失败（数据未下载或不支持该周期）")
        self.write(json.dumps({"period": period, "data": ret}, ensure_ascii=False, default=str))

# ContextInfo.get_hkt_statistics() - 港通统计（个股）
class HktStatisticsHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_code = data.get('stock_code', '')
        ret = safe_call(self.ctx().get_hkt_statistics, stock_code)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        if is_empty_result(ret):
            raise HTTPError(503, "获取港通统计数据失败（数据未下载或该代码不支持）")
        self.write(json.dumps({"stock_code": stock_code, "data": ret}, ensure_ascii=False, default=str))

# ContextInfo.get_hkt_details() - 港通明细（个股，逐日）
class HktDetailsHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_code = data.get('stock_code', '')
        ret = safe_call(self.ctx().get_hkt_details, stock_code)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        if is_empty_result(ret):
            raise HTTPError(503, "获取港通明细失败（数据未下载或该代码不支持）")
        self.write(json.dumps({"stock_code": stock_code, "data": ret}, ensure_ascii=False, default=str))

# 通用只读数据查询：白名单内的 ContextInfo 方法
class QueryHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        method = data.get('method', '')
        if method not in READONLY_CTX_METHODS:
            raise HTTPError(400, "不支持的数据方法：%s（仅允许只读查询）" % method)
        func = getattr(self.ctx(), method, None)
        if func is None:
            raise HTTPError(503, "当前 QMT 环境没有该接口：%s" % method)
        args = data.get('args') or []
        if not isinstance(args, list):
            raise HTTPError(400, "args 必须是数组")
        kwargs = data.get('kwargs') or {}
        if not isinstance(kwargs, dict):
            raise HTTPError(400, "kwargs 必须是对象")
        ret = safe_call(func, *args, **kwargs)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        ret = sanitize_json(ret)
        if is_empty_result(ret):
            raise HTTPError(503, "%s 返回空（数据未下载或参数不符）" % method)
        self.write(json.dumps({"method": method, "data": ret}, ensure_ascii=False, default=str))

# get_top10_share_holder() - 获取十大股东数据
class Top10ShareHolderHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_list = data.get('stock_list', '')
        data_name = data.get('data_name', 'holder')
        start_time = normalize_date8(data.get('start_time', ''), '19720101')
        end_time = normalize_date8(data.get('end_time', ''), '22010101')
        slist = [s.strip() for s in stock_list.split(',')] if stock_list else []
        ret = safe_call(
            self.ctx().get_top10_share_holder, slist, data_name, start_time, end_time
        )
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret} if ret else {"error": "获取十大股东数据失败"}, ensure_ascii=False, default=str))

# ContextInfo.get_option_detail_data() - 获取期权详细信息
class OptionDetailHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        optioncode = data.get('optioncode', '')
        ret = safe_call(self.ctx().get_option_detail_data, optioncode)
        self.write(json.dumps({"optioncode": optioncode, "detail": ret or {}}, ensure_ascii=False))

# ContextInfo.get_turnover_rate() - 获取换手率
class TurnoverRateHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_list = data.get('stock_list', '')
        startTime = normalize_date8(data.get('startTime', ''), '19720101')
        endTime = normalize_date8(data.get('endTime', ''), '22010101')
        slist = [s.strip() for s in stock_list.split(',')] if stock_list else []
        ret = safe_call(self.ctx().get_turnover_rate, slist, startTime, endTime)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret} if ret else {"error": "获取换手率失败"}, ensure_ascii=False, default=str))

# get_etf_info() - 获取ETF申赎清单及成分股
class EtfInfoHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(get_etf_info, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "info": ret or {}}, ensure_ascii=False, default=str))

# get_etf_iopv() - 获取ETF基金份额参考净值
class EtfIopvHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(get_etf_iopv, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "iopv": ret}, ensure_ascii=False))

# ContextInfo.get_instrumentdetail() - 获取合约详细信息
class InstrumentDetailHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().get_instrumentdetail, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "detail": ret or {}}, ensure_ascii=False, default=str))

# ContextInfo.get_contract_expire_date() - 获取期货合约到期日
class ContractExpireDateHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        codemarket = data.get('codemarket', '')
        ret = safe_call(self.ctx().get_contract_expire_date, codemarket)
        self.write(json.dumps({"codemarket": codemarket, "expire_date": ret}, ensure_ascii=False))

# ContextInfo.get_option_undl_data() - 获取期权标的对应的期权品种列表
class OptionUndlDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        undl_code_ref = data.get('undl_code_ref', '')
        ret = safe_call(self.ctx().get_option_undl_data, undl_code_ref)
        self.write(json.dumps({"data": ret or []}, ensure_ascii=False, default=str))

# ContextInfo.get_financial_data() - 获取财务数据
class FinancialDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        tabname = data.get('tabname', '')
        colname = data.get('colname', '')
        market = data.get('market', '')
        code = data.get('code', '')
        report_type = data.get('report_type', 'report_time')
        barpos = int(data.get('barpos', '-1'))
        if tabname and colname and market and code:
            ret = safe_call(self.ctx().get_financial_data, tabname, colname, market, code, report_type, barpos)
        else:
            field_list = data.get('fieldList', '')
            stock_list = data.get('stockList', '')
            start_date = data.get('startDate', '')
            end_date = data.get('endDate', '')
            fields = [f.strip() for f in field_list.split(',')] if field_list else []
            stocks = [s.strip() for s in stock_list.split(',')] if stock_list else []
            rtype = data.get('report_type', 'announce_time')
            ret = safe_call(self.ctx().get_financial_data, fields, stocks, start_date, end_date, rtype)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret} if ret is not None else {"error": "获取财务数据失败"}, ensure_ascii=False, default=str))

# ContextInfo.get_factor_data() - 获取多因子数据
class FactorDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        field_list = data.get('fieldList', '')
        stock_list = data.get('stockList', '')
        stock_code = data.get('stockCode', '')
        start_date = data.get('startDate', '')
        end_date = data.get('endDate', '')
        fields = [f.strip() for f in field_list.split(',')] if field_list else []
        if stock_code:
            ret = safe_call(self.ctx().get_factor_data, fields, stock_code, start_date, end_date)
        else:
            stocks = [s.strip() for s in stock_list.split(',')] if stock_list else []
            ret = safe_call(self.ctx().get_factor_data, fields, stocks, start_date, end_date)
        if hasattr(ret, 'to_dict'):
            ret = ret.to_dict()
        self.write(json.dumps({"data": ret} if ret is not None else {"error": "获取因子数据失败"}, ensure_ascii=False, default=str))

# ContextInfo.get_his_st_data() - 获取历史ST数据
class HisStDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockCode = data.get('stockCode', '')
        ret = safe_call(self.ctx().get_his_st_data, stockCode)
        self.write(json.dumps({"stockCode": stockCode, "data": ret or {}}, ensure_ascii=False))

# ContextInfo.get_his_index_data() - 获取历史指数数据
class HisIndexDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        index = data.get('index', '')
        ret = safe_call(self.ctx().get_his_index_data, index)
        self.write(json.dumps({"index": index, "data": ret or {}}, ensure_ascii=False, default=str))

# ContextInfo.get_all_subscription() - 获取当前所有行情订阅信息
class AllSubscriptionHandler(BaseHandler):
    def get(self):
        ret = safe_call(self.ctx().get_all_subscription)
        self.write(json.dumps({"subscriptions": ret or {}}, ensure_ascii=False, default=str))

# ContextInfo.get_option_list() - 获取指定期权列表
class OptionListHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        undl_code = data.get('undl_code', '')
        dedate = data.get('dedate', '')
        opttype = data.get('opttype', '')
        isavailable = data.get('isavailable', 'true').lower() == 'true'
        ret = safe_call(self.ctx().get_option_list, undl_code, dedate, opttype, isavailable)
        self.write(json.dumps({"option_list": ret or []}, ensure_ascii=False))

# ContextInfo.get_his_contract_list() - 获取过期合约列表
class HisContractListHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        market = data.get('market', '')
        ret = safe_call(self.ctx().get_his_contract_list, market)
        self.write(json.dumps({"market": market, "contracts": ret or []}, ensure_ascii=False))

# ContextInfo.get_option_iv() - 获取期权实时隐含波动率
class OptionIvHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        optioncode = data.get('optioncode', '')
        ret = safe_call(self.ctx().get_option_iv, optioncode)
        self.write(json.dumps({"optioncode": optioncode, "iv": ret}, ensure_ascii=False))

# ContextInfo.bsm_price() - BS模型计算欧式期权理论价格
class BsmPriceHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        optionType = data.get('optionType', 'C')
        objectPrices = data.get('objectPrices', '')
        strikePrice = float(data.get('strikePrice', '0'))
        riskFree = float(data.get('riskFree', '0'))
        sigma = float(data.get('sigma', '0'))
        days = int(data.get('days', '0'))
        dividend = float(data.get('dividend', '0'))
        try:
            op = float(objectPrices)
        except ValueError:
            op = [float(x) for x in objectPrices.split(',')]
        ret = safe_call(self.ctx().bsm_price, optionType, op, strikePrice, riskFree, sigma, days, dividend)
        self.write(json.dumps({"price": ret}, ensure_ascii=False, default=str))

# ContextInfo.bsm_iv() - BS模型计算欧式期权隐含波动率
class BsmIvHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        optionType = data.get('optionType', 'C')
        objectPrices = float(data.get('objectPrices', '0'))
        strikePrice = float(data.get('strikePrice', '0'))
        optionPrice = float(data.get('optionPrice', '0'))
        riskFree = float(data.get('riskFree', '0'))
        days = int(data.get('days', '0'))
        dividend = float(data.get('dividend', '0'))
        ret = safe_call(self.ctx().bsm_iv, optionType, objectPrices, strikePrice, optionPrice, riskFree, days, dividend)
        self.write(json.dumps({"iv": ret}, ensure_ascii=False))

# ContextInfo.get_local_data() - 从本地获取行情数据
class LocalDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_code = data.get('stock_code', '')
        start_time = data.get('start_time', '')
        end_time = data.get('end_time', '')
        period = data.get('period', '1d')
        divid_type = data.get('divid_type', 'none')
        count = int(data.get('count', '-1'))
        ret = safe_call(self.ctx().get_local_data, stock_code, start_time, end_time, period, divid_type, count)
        if ret is None:
            raise HTTPError(500, "获取本地行情失败")
        self.write(json.dumps({"data": ret}, ensure_ascii=False, default=str))

# ContextInfo.subscribe_quote() - 订阅行情数据
class SubscribeQuoteHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stock_code = data.get('stock_code', '')
        period = data.get('period', 'follow')
        dividend_type = data.get('dividend_type', 'follow')
        ret = safe_call(self.ctx().subscribe_quote, stock_code, period, dividend_type)
        self.write(json.dumps({"status": "success" if ret is not None else "failed", "sub_id": ret}, ensure_ascii=False))

# ContextInfo.unsubscribe_quote() - 反订阅行情数据
class UnsubscribeQuoteHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        sub_id = int(data.get('sub_id', '0'))
        safe_call(self.ctx().unsubscribe_quote, sub_id)
        self.write(json.dumps({"status": "success", "sub_id": sub_id}, ensure_ascii=False))


# ============= 3. 判定函数 (is_*) =============
# ContextInfo.is_last_bar() - 判定是否为最后一根K线
class IsLastBarHandler(BaseHandler):
    def get(self):
        ret = safe_call(self.ctx().is_last_bar)
        self.write(json.dumps({"is_last_bar": ret}, ensure_ascii=False))

# ContextInfo.is_new_bar() - 判定是否为新的K线
class IsNewBarHandler(BaseHandler):
    def get(self):
        ret = safe_call(self.ctx().is_new_bar)
        self.write(json.dumps({"is_new_bar": ret}, ensure_ascii=False))

# ContextInfo.is_suspended_stock() - 判定股票是否停牌
class IsSuspendedStockHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stockcode = data.get('stockcode', '')
        ret = safe_call(self.ctx().is_suspended_stock, stockcode)
        self.write(json.dumps({"stockcode": stockcode, "is_suspended": ret}, ensure_ascii=False))

# is_sector_stock() - 判定股票是否在指定板块中
class IsSectorStockHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        sectorname = data.get('sectorname', '')
        market = data.get('market', '')
        stockcode = data.get('stockcode', '')
        ret = safe_call(is_sector_stock, sectorname, market, stockcode)
        self.write(json.dumps({"sectorname": sectorname, "stockcode": stockcode, "is_in_sector": ret}, ensure_ascii=False))

# is_typed_stock() - 判定股票是否属于某个类别
class IsTypedStockHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        stocktypenum = int(data.get('stocktypenum', '0'))
        market = data.get('market', '')
        stockcode = data.get('stockcode', '')
        ret = safe_call(is_typed_stock, stocktypenum, market, stockcode)
        self.write(json.dumps({"stocktypenum": stocktypenum, "stockcode": stockcode, "result": ret}, ensure_ascii=False))

# get_industry_name_of_stock() - 获取股票行业分类名称
class GetIndustryNameOfStockHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        industryType = data.get('industryType', '')
        stockcode = data.get('stockcode', '')
        ret = safe_call(get_industry_name_of_stock, industryType, stockcode)
        self.write(json.dumps({"industryType": industryType, "stockcode": stockcode, "industry_name": ret}, ensure_ascii=False))


# ============= 7. 账户/订单查询 =============
# get_trade_detail_data() - 获取交易明细(持仓/委托/成交/资金)
class TradeDetailDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        datatype = data.get('datatype', 'position')
        ret = safe_call(get_trade_detail_data, self.acc(), account, datatype, 'qmt')
        if ret is None:
            ret = []
        result = []
        for obj in ret:
            attrs = {}
            for attr in dir(obj):
                if not attr.startswith('_'):
                    try:
                        val = getattr(obj, attr)
                        if not callable(val):
                            attrs[attr] = str(val)
                    except Exception:
                        pass
            result.append(attrs)
        self.write(json.dumps({"data": result}, ensure_ascii=False))

# get_value_by_order_id() - 根据委托号获取委托/成交信息
class ValueByOrderIdHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        orderId = data.get('orderId', '')
        accountType = data.get('accountType', 'stock')
        datatype = data.get('datatype', 'ORDER')
        ret = safe_call(get_value_by_order_id, orderId, self.acc(), accountType, datatype)
        attrs = {}
        if ret:
            for attr in dir(ret):
                if not attr.startswith('_'):
                    try:
                        val = getattr(ret, attr)
                        if not callable(val):
                            attrs[attr] = str(val)
                    except Exception:
                        pass
        self.write(json.dumps({"orderId": orderId, "data": attrs}, ensure_ascii=False))

# get_last_order_id() - 获取最新委托/成交的委托号
class LastOrderIdHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        datatype = data.get('datatype', 'ORDER')
        ret = safe_call(get_last_order_id, self.acc(), account, datatype, 'qmt')
        self.write(json.dumps({"last_order_id": ret}, ensure_ascii=False))

# get_debt_contract() - 获取两融负债合约明细
class DebtContractHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        accId = data.get('accId', self.acc())
        ret = safe_call(get_debt_contract, accId)
        result = []
        if ret:
            for obj in ret:
                attrs = {}
                for attr in dir(obj):
                    if not attr.startswith('_'):
                        try:
                            val = getattr(obj, attr)
                            if not callable(val):
                                attrs[attr] = str(val)
                        except Exception:
                            pass
                result.append(attrs)
        self.write(json.dumps({"data": result}, ensure_ascii=False))

# get_assure_contract() - 获取两融担保标的明细
class AssureContractHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        accId = data.get('accId', self.acc())
        ret = safe_call(get_assure_contract, accId)
        result = []
        if ret:
            for obj in ret:
                attrs = {}
                for attr in dir(obj):
                    if not attr.startswith('_'):
                        try:
                            val = getattr(obj, attr)
                            if not callable(val):
                                attrs[attr] = str(val)
                        except Exception:
                            pass
                result.append(attrs)
        self.write(json.dumps({"data": result}, ensure_ascii=False))

# get_enable_short_contract() - 获取可融券明细
class EnableShortContractHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        accId = data.get('accId', self.acc())
        ret = safe_call(get_enable_short_contract, accId)
        result = []
        if ret:
            for obj in ret:
                attrs = {}
                for attr in dir(obj):
                    if not attr.startswith('_'):
                        try:
                            val = getattr(obj, attr)
                            if not callable(val):
                                attrs[attr] = str(val)
                        except Exception:
                            pass
                result.append(attrs)
        self.write(json.dumps({"data": result}, ensure_ascii=False))

# get_ipo_data() - 获取当日新股新债信息
class IpoDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        typ = data.get('type', '')
        ret = safe_call(get_ipo_data, typ)
        self.write(json.dumps({"data": ret or {}}, ensure_ascii=False, default=str))

# get_new_purchase_limit() - 获取新股申购额度
class NewPurchaseLimitHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        accid = data.get('accid', self.acc())
        ret = safe_call(get_new_purchase_limit, accid)
        self.write(json.dumps({"data": ret or {}}, ensure_ascii=False, default=str))


# ============= 8. 引用函数 (ext_data) =============
# ext_data() - 获取扩展数据数值
class ExtDataHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        extdataname = data.get('extdataname', '')
        stockcode = data.get('stockcode', '')
        deviation = int(data.get('deviation', '0'))
        ret = safe_call(ext_data, extdataname, stockcode, deviation, self.ctx())
        self.write(json.dumps({"extdataname": extdataname, "stockcode": stockcode, "value": ret}, ensure_ascii=False))

# ext_data_rank() - 获取扩展数据排名
class ExtDataRankHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        extdataname = data.get('extdataname', '')
        stockcode = data.get('stockcode', '')
        deviation = int(data.get('deviation', '0'))
        ret = safe_call(ext_data_rank, extdataname, stockcode, deviation, self.ctx())
        self.write(json.dumps({"extdataname": extdataname, "stockcode": stockcode, "rank": ret}, ensure_ascii=False))

# get_factor_value() - 获取因子数据
class GetFactorValueHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        factorname = data.get('factorname', '')
        stockcode = data.get('stockcode', '')
        deviation = int(data.get('deviation', '0'))
        ret = safe_call(get_factor_value, factorname, stockcode, deviation, self.ctx())
        self.write(json.dumps({"factorname": factorname, "stockcode": stockcode, "value": ret}, ensure_ascii=False))

# get_factor_rank() - 获取因子数据排名
class GetFactorRankHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        factorname = data.get('factorname', '')
        stockcode = data.get('stockcode', '')
        deviation = int(data.get('deviation', '0'))
        ret = safe_call(get_factor_rank, factorname, stockcode, deviation, self.ctx())
        self.write(json.dumps({"factorname": factorname, "stockcode": stockcode, "rank": ret}, ensure_ascii=False))


# ============= 9. 原有 Handler（保持兼容） =============
# get_trade_detail_data('position') - 查询持仓列表(封装格式)
class HoldingHandler(BaseHandler):
    def post(self):
        require_account(self)
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        positions = safe_call(get_trade_detail_data, self.acc(), account, 'position') or []
        holding = {}
        for position in positions:
            stock = position.m_strInstrumentID + '.' + position.m_strExchangeID
            holding[stock] = {
                'StockCode': stock,
                'StockName': position.m_strInstrumentName,
                'Direction': position.m_nDirection,
                'Volume': position.m_nVolume,
                'OpenPrice': position.m_dOpenPrice,
                'FloatProfit': position.m_dFloatProfit,
                'MarketValue': position.m_dMarketValue,
                'StockHolder': position.m_strStockHolder,
                'FrozenVolume': position.m_nFrozenVolume,
                'CanUseVolume': position.m_nCanUseVolume,
                'OnRoadVolume': position.m_nOnRoadVolume,
                'YesterdayVolume': position.m_nYesterdayVolume,
                'LastPrice': position.m_dLastPrice,
                'ProfitRate': position.m_dProfitRate,
                'FutureTradeType': position.m_eFutureTradeType,
                'ExpireDate': position.m_strExpireDate
            }
        self.write(json.dumps(holding, ensure_ascii=False))

# get_trade_detail_data('account') - 查询总资产
class TotalMoneyHandler(BaseHandler):
    def post(self):
        require_account(self)
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        _data = safe_call(get_trade_detail_data, self.acc(), account, 'account')
        info = _data[0] if _data else None
        if not info:
            raise HTTPError(
                503,
                f"资金数据获取失败：账号 {mask_account(self.acc())} 未连通或未登录"
                f"（account_type={account}）"
            )
        self.write(json.dumps({"total_money": round(info.m_dBalance, 2)}, ensure_ascii=False))

# get_trade_detail_data('account') - 查询可用资金
class AvailableMoneyHandler(BaseHandler):
    def post(self):
        require_account(self)
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        _data = safe_call(get_trade_detail_data, self.acc(), account, 'account')
        info = _data[0] if _data else None
        if not info:
            raise HTTPError(
                503,
                f"可用资金获取失败：账号 {mask_account(self.acc())} 未连通或未登录"
                f"（account_type={account}）"
            )
        self.write(json.dumps({"available_money": round(info.m_dAvailable, 2)}, ensure_ascii=False))

# get_trade_detail_data('order') - 查询委托状态列表
class OrderStatusHandler(BaseHandler):
    def post(self):
        require_account(self)
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        orders = safe_call(get_trade_detail_data, self.acc(), account, 'order', 'qmt') or []
        rets = []
        for order in orders:
            rets.append({
                "order_sys_id": order.m_strOrderSysID,
                "status": order.m_nOrderStatus,
                "volume_left": order.m_nVolumeTotal,
                "volume_traded": order.m_nVolumeTraded,
            })
        self.write(json.dumps({"orders": rets}, ensure_ascii=False))

# cancel() - 按股票+数量匹配规则撤单
# sys: Python版本信息
class PythonVersionHandler(BaseHandler):
    def get(self):
        import sys
        version_info = {
            "python_version": sys.version,
            "python_version_info": {
                "major": sys.version_info.major,
                "minor": sys.version_info.minor,
                "micro": sys.version_info.micro,
                "releaselevel": sys.version_info.releaselevel,
                "serial": sys.version_info.serial,
            }
        }
        self.write(json.dumps(version_info, ensure_ascii=False))

# sys: 账号配置与连通性自检（运维排查用；账号已脱敏）
class AccountStatusHandler(BaseHandler):
    def get(self):
        self.write(json.dumps(account_status(self.acc()), ensure_ascii=False))

    def post(self):
        self.write(json.dumps(account_status(self.acc()), ensure_ascii=False))

# sys: 关闭HTTP服务
class ShutdownHandler(BaseHandler):
    def post(self):
        logger.info("收到关闭请求，服务器即将停止...")
        self.write(json.dumps({"status": "success", "message": "服务器正在关闭..."}, ensure_ascii=False))
        self.finish()
        IOLoop.current().add_callback(IOLoop.current().stop)

# get_trade_detail_data('deal') - 查询成交明细
class DealHandler(BaseHandler):
    def post(self):
        data = json.loads(self.request.body)
        account = data.get('account', 'stock')
        deals = safe_call(get_trade_detail_data, self.acc(), account, 'deal', 'qmt') or []
        rets = []
        for deal in deals:
            attrs = {}
            for attr in dir(deal):
                if not attr.startswith('_'):
                    try:
                        val = getattr(deal, attr)
                        if not callable(val):
                            attrs[attr] = str(val)
                    except Exception:
                        pass
            rets.append(attrs)
        self.write(json.dumps({"deals": rets}, ensure_ascii=False))


# ============= 路由注册 =============
def make_app():
    return Application([

        # 原有兼容路由
        (r"/api/holding", HoldingHandler),
        (r"/api/money/total", TotalMoneyHandler),
        (r"/api/money/available", AvailableMoneyHandler),
        (r"/api/order/status", OrderStatusHandler),
        (r"/api/order/deal", DealHandler),

        # ContextInfo 属性
        (r"/api/context/period", ContextPeriodHandler),
        (r"/api/context/barpos", ContextBarposHandler),
        (r"/api/context/time_tick_size", ContextTimeTickSizeHandler),
        (r"/api/context/stockcode", ContextStockCodeHandler),
        (r"/api/context/dividend_type", ContextDividendTypeHandler),
        (r"/api/context/market", ContextMarketHandler),
        (r"/api/context/do_back_test", ContextDoBackTestHandler),
        (r"/api/context/benchmark", ContextBenchmarkHandler),
        (r"/api/context/capital", ContextCapitalHandler),
        (r"/api/context/universe", ContextUniverseHandler),

        # 数据查询
        (r"/api/data/stock_name", StockNameHandler),
        (r"/api/data/open_date", OpenDateHandler),
        (r"/api/data/last_volume", LastVolumeHandler),
        (r"/api/data/bar_timetag", BarTimetagHandler),
        (r"/api/data/tick_timetag", TickTimetagHandler),
        (r"/api/data/sector", SectorHandler),
        (r"/api/data/industry", IndustryHandler),
        (r"/api/data/stock_list_in_sector", StockListInSectorHandler),
        (r"/api/data/weight_in_index", WeightInIndexHandler),
        (r"/api/data/contract_multiplier", ContractMultiplierHandler),
        (r"/api/data/risk_free_rate", RiskFreeRateHandler),
        (r"/api/data/date_location", DateLocationHandler),
        (r"/api/data/history_data", HistoryDataHandler),
        (r"/api/data/market_data", MarketDataHandler),
        (r"/api/data/market_data_ex", MarketDataExHandler),
        (r"/api/data/full_tick", FullTickHandler),
        (r"/api/data/divid_factors", DividFactorsHandler),
        (r"/api/data/main_contract", MainContractHandler),
        (r"/api/data/timetag_to_datetime", TimetagToDatetimeHandler),
        (r"/api/data/total_share", TotalShareHandler),
        (r"/api/data/trading_dates", TradingDatesHandler),
        (r"/api/data/svol", SvolHandler),
        (r"/api/data/bvol", BvolHandler),
        (r"/api/data/longhubang", LonghubangHandler),
        (r"/api/data/top10_share_holder", Top10ShareHolderHandler),
        (r"/api/data/north_finance_change", NorthFinanceChangeHandler),
        (r"/api/data/hkt_statistics", HktStatisticsHandler),
        (r"/api/data/hkt_details", HktDetailsHandler),
        (r"/api/data/query", QueryHandler),
        (r"/api/data/option_detail", OptionDetailHandler),
        (r"/api/data/turnover_rate", TurnoverRateHandler),
        (r"/api/data/etf_info", EtfInfoHandler),
        (r"/api/data/etf_iopv", EtfIopvHandler),
        (r"/api/data/instrumentdetail", InstrumentDetailHandler),
        (r"/api/data/contract_expire_date", ContractExpireDateHandler),
        (r"/api/data/option_undl_data", OptionUndlDataHandler),
        (r"/api/data/financial_data", FinancialDataHandler),
        (r"/api/data/factor_data", FactorDataHandler),
        (r"/api/data/his_st_data", HisStDataHandler),
        (r"/api/data/his_index_data", HisIndexDataHandler),
        (r"/api/data/all_subscription", AllSubscriptionHandler),
        (r"/api/data/option_list", OptionListHandler),
        (r"/api/data/his_contract_list", HisContractListHandler),
        (r"/api/data/option_iv", OptionIvHandler),
        (r"/api/data/bsm_price", BsmPriceHandler),
        (r"/api/data/bsm_iv", BsmIvHandler),
        (r"/api/data/local_data", LocalDataHandler),

        # 订阅
        (r"/api/data/subscribe_quote", SubscribeQuoteHandler),
        (r"/api/data/unsubscribe_quote", UnsubscribeQuoteHandler),

        # 判定函数
        (r"/api/check/is_last_bar", IsLastBarHandler),
        (r"/api/check/is_new_bar", IsNewBarHandler),
        (r"/api/check/is_suspended_stock", IsSuspendedStockHandler),
        (r"/api/check/is_sector_stock", IsSectorStockHandler),
        (r"/api/check/is_typed_stock", IsTypedStockHandler),
        (r"/api/check/get_industry_name_of_stock", GetIndustryNameOfStockHandler),

        # 账户/订单查询（只读：成交明细、委托查询、两融查询、打新数据）
        (r"/api/trade/trade_detail_data", TradeDetailDataHandler),
        (r"/api/trade/value_by_order_id", ValueByOrderIdHandler),
        (r"/api/trade/last_order_id", LastOrderIdHandler),
        (r"/api/trade/debt_contract", DebtContractHandler),
        (r"/api/trade/assure_contract", AssureContractHandler),
        (r"/api/trade/enable_short_contract", EnableShortContractHandler),
        (r"/api/trade/ipo_data", IpoDataHandler),
        (r"/api/trade/new_purchase_limit", NewPurchaseLimitHandler),

        # 引用函数
        (r"/api/ext/ext_data", ExtDataHandler),
        (r"/api/ext/ext_data_rank", ExtDataRankHandler),
        (r"/api/ext/get_factor_value", GetFactorValueHandler),
        (r"/api/ext/get_factor_rank", GetFactorRankHandler),

        # 系统
        (r"/api/sys/python_version", PythonVersionHandler),
        (r"/api/sys/account_status", AccountStatusHandler),
        (r"/api/sys/shutdown", ShutdownHandler),

    ], debug=False)


def init(ContextInfo):
    try:
        ContextInfo.accountID = ACCOUNT_ID
        app = make_app()
        app.ContextInfo = ContextInfo
        app.accountID = ContextInfo.accountID

        if is_port_in_use(PORT):
            logger.error(
                "端口 %s 已被占用：通常是上一个策略实例未完全退出（QMT 停止策略后"
                "套接字可能仍被占用，或客户端里有第二个本策略实例）。"
                "请完全退出并重启 QMT 客户端（确认任务管理器中无 XtItClient.exe），"
                "再运行本策略，且只运行一个实例。", PORT
            )
            return
        log_startup_self_check()
        app.listen(PORT, address='0.0.0.0')
        logger.info(f"QMT HTTP Server 启动于 http://0.0.0.0:{PORT} (全部API已加载)")
        IOLoop.current().start()
    except Exception as e:
        if "10048" in str(e):
            logger.error(
                "端口 %s 被占用（WinError 10048）：请完全重启 QMT 客户端后重试", PORT
            )
        logger.exception(f"server start failed: {e}")
