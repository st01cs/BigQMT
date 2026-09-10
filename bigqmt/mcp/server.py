# -*- coding: utf-8 -*-
"""BigQMT MCP Server - 基于 FastMCP 的迅投 QMT 量化交易服务。

功能：把 QMT HTTP API（`bigqmt.service.http`，默认 127.0.0.1:10086）封装为 MCP 服务，
暴露 56 个 tools + 2 个 resources，覆盖 A 股/期货/期权的行情查询、账户管理、交易执行，
以及北向资金/港通资金流（get_north_finance_change、get_hkt_statistics、get_hkt_details）。

迁移来源：QMT/mcp_server/qmt_mcp_server.py（保持工具与资源集合不变）。
配置见 `bigqmt.mcp.config`，HTTP 客户端见 `bigqmt.mcp.client`。
启动：`python -m bigqmt.mcp`（默认仅监听 127.0.0.1，对外开放需 --allow-remote）。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from fastmcp import FastMCP

from bigqmt.mcp.client import QMTApiError, get_client
from bigqmt.mcp.config import McpConfig, load_mcp_config

logger = logging.getLogger("bigqmt.mcp")

# 服务元信息（名称/版本/instructions）来自配置，默认与迁移前一致。
MCP_CONFIG: McpConfig = load_mcp_config()

# ===================================
# MCP Server 初始化
# ===================================
mcp = FastMCP(
    name=MCP_CONFIG.name,
    version=MCP_CONFIG.version,
    instructions=MCP_CONFIG.instructions
)


def _resolve_market(stockcode: str, market: str = "") -> str:
    """推导 QMT 市场代码。

    后端 `/api/data/trading_dates` 会把该形参直接透传给
    `ContextInfo.get_trading_dates(market, ...)`，因此必须传 'SH'/'SZ'/'BJ'，
    传股票代码只会得到空列表。
    """
    if market:
        return market.strip().upper()
    code = (stockcode or "").strip().upper()
    if code in {"SH", "SZ", "BJ"}:
        return code
    if "." in code:
        suffix = code.rsplit(".", 1)[-1]
        if suffix in {"SH", "SZ", "BJ"}:
            return suffix
    return "SH"

# ===================================
# MCP Tools - 数据查询类（基础数据）
# ===================================

@mcp.tool()
def get_stock_name(stockcode: str) -> Dict[str, Any]:
    """
    根据股票代码获取股票名称
    
    Args:
        stockcode: 股票代码，如 '600000.SH'
    
    Returns:
        股票名称
    """
    client = get_client()
    result = client._req('POST', '/api/data/stock_name', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_open_date(stockcode: str) -> Dict[str, Any]:
    """
    根据股票代码获取上市时间
    
    Args:
        stockcode: 股票代码
    
    Returns:
        上市时间
    """
    client = get_client()
    result = client._req('POST', '/api/data/open_date', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_last_volume(stockcode: str) -> Dict[str, Any]:
    """
    获取最新流通股本
    
    Args:
        stockcode: 股票代码
    
    Returns:
        流通股本
    """
    client = get_client()
    result = client._req('POST', '/api/data/last_volume', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_bar_timetag(index: int) -> Dict[str, Any]:
    """
    获取K线时间戳
    
    Args:
        index: K线索引
    
    Returns:
        K线时间戳
    """
    client = get_client()
    result = client._req('POST', '/api/data/bar_timetag', json={"index": index})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_tick_timetag() -> Dict[str, Any]:
    """
    获取最新分笔时间戳
    
    Returns:
        分笔时间戳
    """
    client = get_client()
    result = client._req('GET', '/api/data/tick_timetag')
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_date_location(strdate: str) -> Dict[str, Any]:
    """
    获取日期对应的K线索引
    
    Args:
        strdate: 日期字符串，如 '2024-01-01'
    
    Returns:
        K线索引
    """
    client = get_client()
    result = client._req('POST', '/api/data/date_location', json={"strdate": strdate})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_contract_multiplier(contractcode: str) -> Dict[str, Any]:
    """
    获取合约乘数（期货/期权）
    
    Args:
        contractcode: 合约代码
    
    Returns:
        合约乘数
    """
    client = get_client()
    result = client._req('POST', '/api/data/contract_multiplier', json={"contractcode": contractcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_risk_free_rate(index: int = -1) -> Dict[str, Any]:
    """
    获取无风险利率
    
    Args:
        index: 利率索引，默认 -1
    
    Returns:
        无风险利率
    """
    client = get_client()
    result = client._req('POST', '/api/data/risk_free_rate', json={"index": index})
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 数据查询类（行情数据）
# ===================================

@mcp.tool()
def get_realtime_quote(stocks: List[str]) -> Dict[str, Any]:
    """
    获取实时行情数据（分笔数据）
    
    Args:
        stocks: 股票代码列表，如 ['600000.SH', '000001.SZ']
    
    Returns:
        实时行情数据（价格/成交量/买卖盘口等）
    
    Example:
        >>> get_realtime_quote(['600000.SH'])
    """
    client = get_client()
    result = client.get_full_tick(stocks)
    return {
        "timestamp": datetime.now().isoformat(),
        "data": result
    }


@mcp.tool()
def get_market_extended(
    stocks: List[str],
    period: str = 'follow',
    fields: str = '',
    start_time: str = '',
    end_time: str = '',
    count: int = -1,
    dividend_type: str = 'follow'
) -> Dict[str, Any]:
    """
    获取扩展市场数据（多周期 K 线/财务/因子等）
    
    Args:
        stocks: 股票代码列表
        period: K线周期，如 '1d', '1h', '1m'
        fields: 字段列表，逗号分隔
        start_time: 开始时间
        end_time: 结束时间
        count: 数据条数
        dividend_type: 复权类型
    
    Returns:
        扩展市场数据
    """
    client = get_client()
    result = client.get_market_data_ex(
        stock_code=stocks, period=period, fields=fields,
        start_time=start_time, end_time=end_time,
        count=count, dividend_type=dividend_type
    )
    return {
        "timestamp": datetime.now().isoformat(),
        "data": result
    }


@mcp.tool()
def get_history_data(
    stocks: List[str],
    period: str = '1d',
    field: str = 'close',
    dividend_type: int = 0,
    skip_paused: bool = True,
    length: int = 10
) -> Dict[str, Any]:
    """
    获取历史行情数据（多品种字典）
    
    Args:
        stocks: 股票代码列表
        period: K线周期
        field: 字段名
        dividend_type: 复权类型
        skip_paused: 是否跳过停牌
        length: 数据条数
    
    Returns:
        历史行情数据

    Note:
        后端 `/api/data/history_data` 只接受当前上下文的品种（不接受代码列表），
        因此 `stocks` 参数目前不生效，保留仅为接口兼容。
    """
    client = get_client()
    result = client._req('POST', '/api/data/history_data', json={
        "stocks": stocks, "period": period, "field": field,
        "dividend_type": dividend_type, "skip_paused": skip_paused,
        "len": length
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_market_data(
    stocks: List[str],
    fields: List[str],
    start_time: str = '',
    end_time: str = '',
    period: str = '1d',
    dividend_type: str = 'none',
    count: int = -1
) -> Dict[str, Any]:
    """
    获取行情数据（DataFrame格式）
    
    Args:
        stocks: 股票代码列表
        fields: 字段列表
        start_time: 开始时间
        end_time: 结束时间
        period: K线周期
        dividend_type: 复权类型
        count: 数据条数
    
    Returns:
        行情数据（DataFrame）
    """
    client = get_client()
    result = client.get_market_data(
        stock_code=stocks, fields=fields,
        start_time=start_time, end_time=end_time,
        period=period, dividend_type=dividend_type,
        count=count
    )
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_divid_factors(stockcode: str) -> Dict[str, Any]:
    """
    获取除权除息和复权因子
    
    Args:
        stockcode: 股票代码
    
    Returns:
        复权因子
    """
    client = get_client()
    result = client._req('POST', '/api/data/divid_factors', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_main_contract(codemarket: str) -> Dict[str, Any]:
    """
    获取期货主力合约
    
    Args:
        codemarket: 市场代码
    
    Returns:
        主力合约代码
    """
    client = get_client()
    result = client._req('POST', '/api/data/main_contract', json={"codemarket": codemarket})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def timetag_to_datetime(timetag: int, fmt: str = '%Y-%m-%d %H:%M:%S') -> Dict[str, Any]:
    """
    毫秒时间戳转日期时间
    
    Args:
        timetag: 毫秒时间戳
        fmt: 日期格式
    
    Returns:
        日期时间字符串
    """
    client = get_client()
    result = client._req('POST', '/api/data/timetag_to_datetime', json={"timetag": timetag, "format": fmt})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_total_share(stockcode: str) -> Dict[str, Any]:
    """
    获取总股本
    
    Args:
        stockcode: 股票代码
    
    Returns:
        总股本
    """
    client = get_client()
    result = client._req('POST', '/api/data/total_share', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_trading_dates(
    stockcode: str,
    start_date: str = '',
    end_date: str = '',
    count: int = -1,
    period: str = '1d',
    market: str = ''
) -> Dict[str, Any]:
    """
    获取交易日列表
    
    Args:
        stockcode: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        count: 数据条数
        period: K线周期
    
    Returns:
        交易日列表
    """
    client = get_client()
    result = client.get_trading_dates(
        market=_resolve_market(stockcode, market),
        start_date=start_date, end_date=end_date,
        count=count, period=period
    )
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_svol(stockcode: str) -> Dict[str, Any]:
    """
    获取内盘成交量
    
    Args:
        stockcode: 股票代码
    
    Returns:
        内盘成交量
    """
    client = get_client()
    result = client._req('POST', '/api/data/svol', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_bvol(stockcode: str) -> Dict[str, Any]:
    """
    获取外盘成交量
    
    Args:
        stockcode: 股票代码
    
    Returns:
        外盘成交量
    """
    client = get_client()
    result = client._req('POST', '/api/data/bvol', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_local_data(
    stock_code: str,
    start_time: str = '',
    end_time: str = '',
    period: str = '1d',
    divid_type: str = 'none',
    count: int = -1
) -> Dict[str, Any]:
    """
    从本地获取行情数据
    
    Args:
        stock_code: 股票代码
        start_time: 开始时间
        end_time: 结束时间
        period: K线周期
        divid_type: 复权类型
        count: 数据条数
    
    Returns:
        本地行情数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/local_data', json={
        "stock_code": stock_code, "start_time": start_time,
        "end_time": end_time, "period": period,
        "divid_type": divid_type, "count": count
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def subscribe_quote(
    stock_code: str,
    period: str = 'follow',
    dividend_type: str = 'follow'
) -> Dict[str, Any]:
    """
    订阅行情数据
    
    Args:
        stock_code: 股票代码
        period: K线周期
        dividend_type: 复权类型
    
    Returns:
        订阅状态和订阅ID
    """
    client = get_client()
    result = client._req('POST', '/api/data/subscribe_quote', json={
        "stock_code": stock_code, "period": period,
        "dividend_type": dividend_type
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def unsubscribe_quote(sub_id: int) -> Dict[str, Any]:
    """
    反订阅行情数据
    
    Args:
        sub_id: 订阅ID
    
    Returns:
        反订阅状态
    """
    client = get_client()
    result = client._req('POST', '/api/data/unsubscribe_quote', json={"sub_id": sub_id})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_all_subscription() -> Dict[str, Any]:
    """
    获取当前所有行情订阅信息
    
    Returns:
        所有订阅信息
    """
    client = get_client()
    result = client._req('GET', '/api/data/all_subscription')
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 数据查询类（板块数据）
# ===================================

@mcp.tool()
def get_sector(sector: str, realtime: int = 0) -> Dict[str, Any]:
    """
    获取指数成份股
    
    Args:
        sector: 指数代码
        realtime: 是否实时
    
    Returns:
        成份股列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/sector', json={"sector": sector, "realtime": realtime})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_industry(industry: str) -> Dict[str, Any]:
    """
    获取行业成份股
    
    Args:
        industry: 行业代码
    
    Returns:
        成份股列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/industry', json={"industry": industry})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_stock_list_in_sector(sectorname: str) -> Dict[str, Any]:
    """
    获取板块成份股
    
    Args:
        sectorname: 板块名称
    
    Returns:
        成份股列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/stock_list_in_sector', json={"sectorname": sectorname})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_weight_in_index(indexcode: str, stockcode: str) -> Dict[str, Any]:
    """
    获取指数中权重
    
    Args:
        indexcode: 指数代码
        stockcode: 股票代码
    
    Returns:
        权重
    """
    client = get_client()
    result = client._req('POST', '/api/data/weight_in_index', json={
        "indexcode": indexcode, "stockcode": stockcode
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 数据查询类（财务数据）
# ===================================

@mcp.tool()
def get_financial_data(
    tabname: str = '',
    colname: str = '',
    market: str = '',
    code: str = '',
    report_type: str = 'report_time',
    barpos: int = -1,
    fieldList: str = '',
    stockList: str = '',
    startDate: str = '',
    endDate: str = ''
) -> Dict[str, Any]:
    """
    获取财务数据
    
    Args:
        tabname: 表名
        colname: 列名
        market: 市场
        code: 代码
        report_type: 报告类型
        barpos: K线索引
        fieldList: 字段列表
        stockList: 股票列表
        startDate: 开始日期
        endDate: 结束日期
    
    Returns:
        财务数据
    """
    client = get_client()
    if tabname and colname and market and code:
        result = client._req('POST', '/api/data/financial_data', json={
            "tabname": tabname, "colname": colname,
            "market": market, "code": code,
            "report_type": report_type, "barpos": barpos
        })
    else:
        result = client._req('POST', '/api/data/financial_data', json={
            "fieldList": fieldList, "stockList": stockList,
            "startDate": startDate, "endDate": endDate,
            "report_type": report_type
        })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_factor_data(
    fieldList: str,
    stockCode: str = '',
    stockList: str = '',
    startDate: str = '',
    endDate: str = ''
) -> Dict[str, Any]:
    """
    获取多因子数据
    
    Args:
        fieldList: 因子字段列表
        stockCode: 单个股票代码
        stockList: 股票列表
        startDate: 开始日期
        endDate: 结束日期
    
    Returns:
        多因子数据
    """
    client = get_client()
    if stockCode:
        result = client._req('POST', '/api/data/factor_data', json={
            "fieldList": fieldList, "stockCode": stockCode,
            "startDate": startDate, "endDate": endDate
        })
    else:
        result = client._req('POST', '/api/data/factor_data', json={
            "fieldList": fieldList, "stockList": stockList,
            "startDate": startDate, "endDate": endDate
        })
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 数据查询类（期权数据）
# ===================================

@mcp.tool()
def get_option_detail_data(optioncode: str) -> Dict[str, Any]:
    """
    获取期权详细信息
    
    Args:
        optioncode: 期权代码
    
    Returns:
        期权详细信息
    """
    client = get_client()
    result = client._req('POST', '/api/data/option_detail', json={"optioncode": optioncode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_option_list(
    undl_code: str,
    dedate: str = '',
    opttype: str = '',
    isavailable: bool = True
) -> Dict[str, Any]:
    """
    获取指定期权列表
    
    Args:
        undl_code: 标的代码
        dedate: 到期日
        opttype: 期权类型
        isavailable: 是否可用
    
    Returns:
        期权列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/option_list', json={
        "undl_code": undl_code, "dedate": dedate,
        "opttype": opttype, "isavailable": isavailable
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_his_contract_list(market: str) -> Dict[str, Any]:
    """
    获取过期合约列表
    
    Args:
        market: 市场代码
    
    Returns:
        过期合约列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/his_contract_list', json={"market": market})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_option_iv(optioncode: str) -> Dict[str, Any]:
    """
    获取期权实时隐含波动率
    
    Args:
        optioncode: 期权代码
    
    Returns:
        隐含波动率
    """
    client = get_client()
    result = client._req('POST', '/api/data/option_iv', json={"optioncode": optioncode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_option_undl_data(undl_code_ref: str) -> Dict[str, Any]:
    """
    获取期权标的对应的期权品种列表
    
    Args:
        undl_code_ref: 标代码
    
    Returns:
        期权品种列表
    """
    client = get_client()
    result = client._req('POST', '/api/data/option_undl_data', json={"undl_code_ref": undl_code_ref})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def bsm_price(
    optionType: str = 'C',
    objectPrices: str = '',
    strikePrice: float = 0,
    riskFree: float = 0,
    sigma: float = 0,
    days: int = 0,
    dividend: float = 0
) -> Dict[str, Any]:
    """
    BS模型计算欧式期权理论价格
    
    Args:
        optionType: 期权类型（C=看涨，P=看跌）
        objectPrices: 标的价格
        strikePrice: 行权价
        riskFree: 无风险利率
        sigma: 波动率
        days: 剩余天数
        dividend: 股息率
    
    Returns:
        BS理论价格
    """
    client = get_client()
    result = client._req('POST', '/api/data/bsm_price', json={
        "optionType": optionType, "objectPrices": objectPrices,
        "strikePrice": strikePrice, "riskFree": riskFree,
        "sigma": sigma, "days": days, "dividend": dividend
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def bsm_iv(
    optionType: str = 'C',
    objectPrices: float = 0,
    strikePrice: float = 0,
    optionPrice: float = 0,
    riskFree: float = 0,
    days: int = 0,
    dividend: float = 0
) -> Dict[str, Any]:
    """
    BS模型计算欧式期权隐含波动率
    
    Args:
        optionType: 期权类型
        objectPrices: 标的价格
        strikePrice: 行权价
        optionPrice: 期权价格
        riskFree: 无风险利率
        days: 剩余天数
        dividend: 股息率
    
    Returns:
        隐含波动率
    """
    client = get_client()
    result = client._req('POST', '/api/data/bsm_iv', json={
        "optionType": optionType, "objectPrices": objectPrices,
        "strikePrice": strikePrice, "optionPrice": optionPrice,
        "riskFree": riskFree, "days": days, "dividend": dividend
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 数据查询类（特殊数据）
# ===================================

@mcp.tool()
def get_longhubang(
    stock_list: str,
    startTime: str = '',
    endTime: str = ''
) -> Dict[str, Any]:
    """
    获取龙虎榜数据
    
    Args:
        stock_list: 股票列表
        startTime: 开始时间
        endTime: 结束时间
    
    Returns:
        龙虎榜数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/longhubang', json={
        "stock_list": stock_list, "startTime": startTime,
        "endTime": endTime
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_top10_share_holder(
    stock_list: str,
    data_name: str = 'holder',
    start_time: str = '',
    end_time: str = ''
) -> Dict[str, Any]:
    """
    获取十大股东数据
    
    Args:
        stock_list: 股票列表
        data_name: 数据类型
        start_time: 开始时间
        end_time: 结束时间
    
    Returns:
        十大股东数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/top10_share_holder', json={
        "stock_list": stock_list, "data_name": data_name,
        "start_time": start_time, "end_time": end_time
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_turnover_rate(
    stock_list: str,
    startTime: str = '',
    endTime: str = ''
) -> Dict[str, Any]:
    """
    获取换手率
    
    Args:
        stock_list: 股票列表
        startTime: 开始时间
        endTime: 结束时间
    
    Returns:
        换手率数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/turnover_rate', json={
        "stock_list": stock_list, "startTime": startTime,
        "endTime": endTime
    })
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_north_finance_change(period: str = '1d') -> Dict[str, Any]:
    """
    获取北向资金（陆股通）资金变化，市场级每日流入/流出

    Args:
        period: 周期，如 '1d'

    Returns:
        北向资金变化数据

    Example:
        >>> get_north_finance_change('1d')
    """
    client = get_client()
    result = client.get_north_finance_change(period)
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_hkt_statistics(stock_code: str) -> Dict[str, Any]:
    """
    获取个股港通统计（沪/深股通持股与资金统计）

    Args:
        stock_code: 股票代码，如 '601899.SH'

    Returns:
        港通统计数据

    Example:
        >>> get_hkt_statistics('601899.SH')
    """
    client = get_client()
    result = client.get_hkt_statistics(stock_code)
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_hkt_details(stock_code: str) -> Dict[str, Any]:
    """
    获取个股港通明细（逐日流入/流出）

    Args:
        stock_code: 股票代码，如 '601899.SH'

    Returns:
        港通逐日明细数据

    Example:
        >>> get_hkt_details('601899.SH')
    """
    client = get_client()
    result = client.get_hkt_details(stock_code)
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_etf_info(stockcode: str) -> Dict[str, Any]:
    """
    获取ETF申赎清单及成分股
    
    Args:
        stockcode: ETF代码
    
    Returns:
        ETF信息
    """
    client = get_client()
    result = client._req('POST', '/api/data/etf_info', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_etf_iopv(stockcode: str) -> Dict[str, Any]:
    """
    获取ETF基金份额参考净值
    
    Args:
        stockcode: ETF代码
    
    Returns:
        ETF净值
    """
    client = get_client()
    result = client._req('POST', '/api/data/etf_iopv', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_instrument_detail(stockcode: str) -> Dict[str, Any]:
    """
    获取合约详细信息
    
    Args:
        stockcode: 合约代码
    
    Returns:
        合约详细信息
    """
    client = get_client()
    result = client._req('POST', '/api/data/instrumentdetail', json={"stockcode": stockcode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_contract_expire_date(codemarket: str) -> Dict[str, Any]:
    """
    获取期货合约到期日
    
    Args:
        codemarket: 市场代码
    
    Returns:
        到期日
    """
    client = get_client()
    result = client._req('POST', '/api/data/contract_expire_date', json={"codemarket": codemarket})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_his_st_data(stockCode: str) -> Dict[str, Any]:
    """
    获取历史ST数据
    
    Args:
        stockCode: 股票代码
    
    Returns:
        历史ST数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/his_st_data', json={"stockCode": stockCode})
    return {"timestamp": datetime.now().isoformat(), "data": result}


@mcp.tool()
def get_his_index_data(index: str) -> Dict[str, Any]:
    """
    获取历史指数数据
    
    Args:
        index: 指数代码
    
    Returns:
        历史指数数据
    """
    client = get_client()
    result = client._req('POST', '/api/data/his_index_data', json={"index": index})
    return {"timestamp": datetime.now().isoformat(), "data": result}


# ===================================
# MCP Tools - 账户管理类
# ===================================

@mcp.tool()
def get_portfolio_info(account: str = 'stock') -> Dict[str, Any]:
    """
    获取投资组合综合信息
    
    Args:
        account: 账户类型，默认 'stock'
    
    Returns:
        总资产、可用资金、持仓信息
    """
    client = get_client()
    return {
        "timestamp": datetime.now().isoformat(),
        "account": account,
        "total_money": client.get_total_money(account),
        "available_money": client.get_available_money(account),
        "holding": client.get_holding(account)
    }


@mcp.tool()
def get_positions(account: str = 'stock') -> Dict[str, Any]:
    """
    获取当前持仓明细
    
    Args:
        account: 账户类型
    
    Returns:
        持仓股票/期货/期权明细
    """
    client = get_client()
    return {
        "timestamp": datetime.now().isoformat(),
        "account": account,
        "data": client.get_holding(account)
    }


@mcp.tool()
def get_available_funds(account: str = 'stock') -> Dict[str, Any]:
    """
    获取可用资金（可下单金额）
    
    Args:
        account: 账户类型
    
    Returns:
        可用资金数额
    """
    client = get_client()
    return {
        "timestamp": datetime.now().isoformat(),
        "account": account,
        "data": client.get_available_money(account)
    }


@mcp.tool()
def get_total_assets(account: str = 'stock') -> Dict[str, Any]:
    """
    获取账户总资产
    
    Args:
        account: 账户类型
    
    Returns:
        总资产数额
    """
    client = get_client()
    return {
        "timestamp": datetime.now().isoformat(),
        "account": account,
        "data": client.get_total_money(account)
    }


# ===================================
# MCP Tools - 交易执行类
# ===================================

@mcp.tool()
def buy_stock(
    stock: str,
    price: float,
    volume: int,
    pr_type: int = 11
) -> Dict[str, Any]:
    """
    买入股票
    
    Args:
        stock: 股票代码，如 '600000.SH'
        price: 委托价格（pr_type=11 时为指定价）
        volume: 买入数量（股/手）
        pr_type: 选价类型，默认 11（指定价）
                 0-10: 卖5到买5档位价
                 12: 涨跌停价
                 14: 对手价
                 42-49: 多种市价策略
    
    Returns:
        委托结果（委托编号/状态/成交情况）
    
    Warning:
        危险操作：请确认价格、数量后再执行！
    """
    client = get_client()
    result = client.buy_stock(stock, price, volume, pr_type)
    return {
        "timestamp": datetime.now().isoformat(),
        "action": "BUY",
        "stock": stock,
        "price": price,
        "volume": volume,
        "pr_type": pr_type,
        "data": result
    }


@mcp.tool()
def sell_stock(
    stock: str,
    price: float,
    volume: int,
    pr_type: int = 11
) -> Dict[str, Any]:
    """
    卖出股票
    
    Args:
        stock: 股票代码
        price: 委托价格
        volume: 卖出数量
        pr_type: 选价类型
    
    Returns:
        委托结果
    
    Warning:
        危险操作：请确认价格、数量后再执行！
    """
    client = get_client()
    result = client.sell_stock(stock, price, volume, pr_type)
    return {
        "timestamp": datetime.now().isoformat(),
        "action": "SELL",
        "stock": stock,
        "price": price,
        "volume": volume,
        "pr_type": pr_type,
        "data": result
    }


# ===================================
# MCP Tools - 订单管理类
# ===================================

@mcp.tool()
def get_order_status(account: str = 'stock') -> Dict[str, Any]:
    """
    查询委托订单状态
    
    Args:
        account: 账户类型
    
    Returns:
        所有活跃订单状态列表
        状态码：0=待报，50=已报，55=部成，56=已成，54=已撤，57=废单
    """
    client = get_client()
    return {
        "timestamp": datetime.now().isoformat(),
        "account": account,
        "data": client.get_order_status(account)
    }


@mcp.tool()
def cancel_all_orders(account: str = 'stock') -> Dict[str, Any]:
    """
    一键撤销所有活跃订单（危险操作）
    
    Args:
        account: 账户类型
    
    Returns:
        撤单结果
    
    Warning:
        危险操作：将撤销所有未成交订单！
    """
    client = get_client()
    result = client.cancel_all_orders(account)
    return {
        "timestamp": datetime.now().isoformat(),
        "action": "CANCEL_ALL",
        "account": account,
        "data": result
    }


# ===================================
# MCP Resources - 静态数据
# ===================================

@mcp.resource("qmt://info/version")
def get_server_info() -> str:
    """获取 QMT 服务器版本信息。

    后端不可用时返回可用性标记而不是抛错，保证资源读取始终可用。
    """
    client = get_client()
    try:
        result: Any = client.python_version()
        available = True
        error = None
    except QMTApiError as exc:
        result = None
        available = False
        error = str(exc)
    return json.dumps({
        "server": MCP_CONFIG.name,
        "version": MCP_CONFIG.version,
        "qmt_available": available,
        "qmt_error": error,
        "qmt_python": result,
        "qmt_base_url": MCP_CONFIG.qmt_base_url,
        "supported_markets": ["A股", "期货", "期权", "可转债"],
        "transport": "HTTP (Streamable)"
    }, ensure_ascii=False, indent=2)


@mcp.resource("qmt://info/pr_types")
def get_price_types_info() -> str:
    """获取选价类型 (prType) 完整说明"""
    pr_types = {
        "-1": "无效（仅算法单）",
        "0-4": "卖5价到卖1价",
        "5": "最新价",
        "6-10": "买1价到买5价",
        "11": "指定价（模型价）",
        "12": "涨跌停价",
        "13": "挂单价",
        "14": "对手价",
        "18-24": "期货市价单（郑商所/大商所/中金所）",
        "26-29": "期权市价单",
        "42-49": "股票/期权多种市价策略"
    }
    return json.dumps({
        "description": "prType 选价类型完整说明",
        "types": pr_types,
        "default": 11  # 指定价
    }, ensure_ascii=False, indent=2)


# ===================================
# 启动入口
# ===================================

if __name__ == "__main__":  # 兼容 `python -m bigqmt.mcp.server`
    from bigqmt.mcp.cli import main

    raise SystemExit(main())
