"""接入层 Calculator（22 个 tushare 接口 1:1 复刻）。

每个 Calculator 声明 config_key（对应 config/tushare_apis.json），继承对应中间基类：
- 行情类 → TushareByTradeDateCalculator（逐交易日拉）
- 财务类 → TushareByAnnDateCalculator（按 ann_date 区间拉 + 回看覆盖修订）
- 基础信息类 → TushareFullRefreshCalculator（全量 truncate）

特殊接口（需遍历参数）覆盖 fetch_one_period：
- TradeCalCalculator: 全量拉（start_date=20100101, end_date=今天）
- StockBasicCalculator: 遍历 list_status=L/D
- IndexMemberAllCalculator: 遍历 is_new=Y/N
- IndexDailyCalculator / IndexDailyBasicCalculator: 遍历 index_codes
- IndexWeightCalculator: 遍历 index_codes + 月份区间
- IndexClassifyCalculator: src=SW2021

统一入口 update(start_date, end_date, **params)（来自 BaseCalculator）：
- 不传日期 = 从 etl_biz_date 水位次日续跑（增量）
- 传日期 = 按 biz_date 区间回补
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from core.dates import get_today_str
from data.etl.base import (
    TushareByAnnDateCalculator,
    TushareByTradeDateCalculator,
    TushareFullRefreshCalculator,
)

logger = logging.getLogger(__name__)

# 关心的指数代码（沪深主要宽基 + 申万）
INDEX_CODES: List[str] = [
    "000300.SH", "000852.SH", "000905.SH", "000906.SH",
    "000922.CSI", "000985.CSI", "399300.SZ", "399852.SZ",
    "399905.SZ", "930955.CSI", "932000.CSI",
]


# ==================== full_refresh: 基础信息类（5 个） ====================

class TradeCalCalculator(TushareFullRefreshCalculator):
    """交易日历（全量刷新）。"""
    config_key = "trade_cal"
    table_name = "trade_cal"
    primary_keys = ["exchange", "cal_date"]

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        # 全量拉 2010 至今
        return self.fetch_tushare(
            start_date="20100101", end_date=get_today_str(), **params
        )


class StockBasicCalculator(TushareFullRefreshCalculator):
    """股票基本信息（全量刷新，遍历 list_status=L/D）。"""
    config_key = "stock_basic"
    table_name = "stock_basic"
    primary_keys = ["ts_code"]

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        frames = []
        for status in ["L", "D"]:
            df = self.fetch_tushare(list_status=status, **params)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


class IndexBasicCalculator(TushareFullRefreshCalculator):
    """指数基本信息（全量刷新）。"""
    config_key = "index_basic"
    table_name = "index_basic"
    primary_keys = ["ts_code"]


class IndexClassifyCalculator(TushareFullRefreshCalculator):
    """申万行业分类（全量刷新，src=SW2021）。"""
    config_key = "index_classify"
    table_name = "index_classify"
    primary_keys = ["index_code"]

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        params.setdefault("src", "SW2021")
        return self.fetch_tushare(**params)


class IndexMemberAllCalculator(TushareFullRefreshCalculator):
    """指数成分股全量（全量刷新，遍历 is_new=Y/N）。"""
    config_key = "index_member_all"
    table_name = "index_member_all"
    primary_keys = ["ts_code", "l1_code", "in_date"]

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        frames = []
        for is_new in ["Y", "N"]:
            df = self.fetch_tushare(is_new=is_new, **params)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


# ==================== by_trade_date: 行情类（12 个） ====================

class StockDailyCalculator(TushareByTradeDateCalculator):
    """日线行情。"""
    config_key = "daily"
    table_name = "stock_daily"
    primary_keys = ["ts_code", "trade_date"]


class StockWeeklyCalculator(TushareByTradeDateCalculator):
    """周线行情（逐交易日调，非周末返回空自动跳过）。"""
    config_key = "weekly"
    table_name = "stock_weekly"
    primary_keys = ["ts_code", "trade_date"]


class StockMonthlyCalculator(TushareByTradeDateCalculator):
    """月线行情（逐交易日调，非月末返回空自动跳过）。"""
    config_key = "monthly"
    table_name = "stock_monthly"
    primary_keys = ["ts_code", "trade_date"]


class AdjFactorCalculator(TushareByTradeDateCalculator):
    """复权因子。"""
    config_key = "adj_factor"
    table_name = "adj_factor"
    primary_keys = ["ts_code", "trade_date"]


class DailyBasicCalculator(TushareByTradeDateCalculator):
    """每日指标（PE/PB/市值等）。"""
    config_key = "daily_basic"
    table_name = "stock_daily_basic"
    primary_keys = ["ts_code", "trade_date"]


class MoneyflowCalculator(TushareByTradeDateCalculator):
    """资金流向。"""
    config_key = "moneyflow"
    table_name = "moneyflow"
    primary_keys = ["ts_code", "trade_date"]


class StockStCalculator(TushareByTradeDateCalculator):
    """ST 股票信息。"""
    config_key = "stock_st"
    table_name = "stock_st"
    primary_keys = ["ts_code", "trade_date"]


class SuspendDCalculator(TushareByTradeDateCalculator):
    """每日停复牌信息。"""
    config_key = "suspend_d"
    table_name = "suspend"
    primary_keys = ["ts_code", "trade_date"]


class SwDailyCalculator(TushareByTradeDateCalculator):
    """申万行业日线行情。"""
    config_key = "sw_daily"
    table_name = "sw_daily"
    primary_keys = ["ts_code", "trade_date"]


class IndexDailyCalculator(TushareByTradeDateCalculator):
    """指数日线行情（遍历 index_codes）。"""
    config_key = "index_daily"
    table_name = "index_daily"
    primary_keys = ["ts_code", "trade_date"]

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        frames = []
        for code in INDEX_CODES:
            df = self.fetch_tushare(ts_code=code, trade_date=trade_date, **params)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


class IndexDailyBasicCalculator(TushareByTradeDateCalculator):
    """指数每日指标（遍历 index_codes）。"""
    config_key = "index_dailybasic"
    table_name = "index_daily_basic"
    primary_keys = ["ts_code", "trade_date"]

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        frames = []
        for code in INDEX_CODES:
            df = self.fetch_tushare(ts_code=code, trade_date=trade_date, **params)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


class IndexWeightCalculator(TushareByTradeDateCalculator):
    """指数成分股权重（遍历 index_codes + 月份区间）。

    by_trade_date 逐日调，但 index_weight 是月频。fetch_one_period(trade_date=...)
    把 trade_date 当月末，算当月区间 [月初, trade_date] 遍历 index_codes 拉。
    """
    config_key = "index_weight"
    table_name = "index_weight"
    primary_keys = ["index_code", "con_code", "trade_date"]

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        # 月初 = trade_date 当月 1 号
        td = datetime.strptime(trade_date, "%Y%m%d")
        month_start = td.replace(day=1).strftime("%Y%m%d")
        frames = []
        for code in INDEX_CODES:
            df = self.fetch_tushare(
                index_code=code,
                start_date=month_start,
                end_date=trade_date,
                **params,
            )
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


# ==================== by_ann_date: 财务类（5 个） ====================

class IncomeCalculator(TushareByAnnDateCalculator):
    """利润表（按 ann_date 增量）。"""
    config_key = "income_vip"
    table_name = "income"
    primary_keys = ["ts_code", "end_date", "report_type", "comp_type"]


class BalancesheetCalculator(TushareByAnnDateCalculator):
    """资产负债表（按 ann_date 增量）。"""
    config_key = "balancesheet_vip"
    table_name = "balancesheet"
    primary_keys = ["ts_code", "end_date", "report_type", "comp_type"]


class CashflowCalculator(TushareByAnnDateCalculator):
    """现金流量表（按 ann_date 增量）。"""
    config_key = "cashflow_vip"
    table_name = "cashflow"
    primary_keys = ["ts_code", "end_date", "report_type", "comp_type"]


class DividendCalculator(TushareByAnnDateCalculator):
    """分红送股（按 ann_date 增量）。

    tushare dividend 不支持 start_date/end_date 区间，只支持 ann_date 单日。
    覆盖 fetch_one_period 逐 ann_date 调。
    """
    config_key = "dividend"
    table_name = "dividend"
    primary_keys = ["ts_code", "end_date", "ann_date", "div_proc"]

    def fetch_one_period(
        self, start_ann_date: str, end_ann_date: str, **params
    ) -> Optional[pd.DataFrame]:
        # dividend 不支持区间，逐日调 ann_date（公告日不一定是交易日，枚举自然日）
        start = datetime.strptime(start_ann_date, "%Y%m%d")
        end = datetime.strptime(end_ann_date, "%Y%m%d")
        frames = []
        cur = start
        while cur <= end:
            ann_d = cur.strftime("%Y%m%d")
            df = self.fetch_tushare(ann_date=ann_d, **params)
            if df is not None and not df.empty:
                frames.append(df)
            cur += timedelta(days=1)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


class DisclosureDateCalculator(TushareByAnnDateCalculator):
    """财报披露日期（按 ann_date 增量）。

    tushare disclosure_date 支持 end_date（报告期）但不支持 ann_date 区间。
    用 ann_date 单日逐日调（同 dividend）。
    """
    config_key = "disclosure_date"
    table_name = "disclosure_date"
    primary_keys = ["ts_code", "end_date"]

    def fetch_one_period(
        self, start_ann_date: str, end_ann_date: str, **params
    ) -> Optional[pd.DataFrame]:
        start = datetime.strptime(start_ann_date, "%Y%m%d")
        end = datetime.strptime(end_ann_date, "%Y%m%d")
        frames = []
        cur = start
        while cur <= end:
            ann_d = cur.strftime("%Y%m%d")
            df = self.fetch_tushare(ann_date=ann_d, **params)
            if df is not None and not df.empty:
                frames.append(df)
            cur += timedelta(days=1)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)


# ===== 任务注册表（供 runner 通过 class_path 找到） =====
CALCULATORS = {
    "trade_cal": TradeCalCalculator,
    "stock_basic": StockBasicCalculator,
    "stock_st": StockStCalculator,
    "suspend_d": SuspendDCalculator,
    "daily": StockDailyCalculator,
    "weekly": StockWeeklyCalculator,
    "monthly": StockMonthlyCalculator,
    "adj_factor": AdjFactorCalculator,
    "daily_basic": DailyBasicCalculator,
    "moneyflow": MoneyflowCalculator,
    "index_basic": IndexBasicCalculator,
    "index_daily": IndexDailyCalculator,
    "index_dailybasic": IndexDailyBasicCalculator,
    "index_weight": IndexWeightCalculator,
    "index_classify": IndexClassifyCalculator,
    "index_member_all": IndexMemberAllCalculator,
    "sw_daily": SwDailyCalculator,
    "income": IncomeCalculator,
    "balancesheet": BalancesheetCalculator,
    "cashflow": CashflowCalculator,
    "dividend": DividendCalculator,
    "disclosure_date": DisclosureDateCalculator,
}
