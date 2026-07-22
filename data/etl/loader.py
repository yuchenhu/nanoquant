"""接入层 Calculator（31 个 tushare 接口 1:1 复刻）。

每个 Calculator 声明 config_key（对应 config/tushare_apis.json），继承对应中间基类：
- 行情类 → TushareByTradeDateCalculator（逐交易日 overwrite）
- 财务三表+披露 → TushareByPeriodCalculator（按报告期 period 取全市场 overwrite/end_date）
- 分红 → TushareByExDateCalculator（按除权日 ex_date 取 overwrite/ex_date）
- 基础信息/ETF清单 → TushareFullRefreshCalculator（全量 truncate）

特殊接口（需遍历参数）覆盖 fetch_one_period / get_data：
- TradeCalCalculator: 全量拉（start_date=20100101, end_date=今天）
- StockBasicCalculator: 遍历 list_status=L/D
- IndexMemberAllCalculator: 遍历 is_new=Y/N
- IndexDailyCalculator / IndexDailyBasicCalculator: 遍历 INDEX_CODES × 区间取数（_IndexByRangeMixin）
- IndexWeightCalculator: 月频，遍历 INDEX_CODES，每月最后交易日取一次
- IndexClassifyCalculator: src=SW2021

统一入口 update(start_date, end_date, **params)（来自 BaseCalculator）：
- 不传日期 = 增量（行情从水位续跑；财务/分红从 min(水位, today-保守窗口) 起）
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
    TushareByExDateCalculator,
    TushareByPeriodCalculator,
    TushareByTradeDateCalculator,
    TushareFullRefreshCalculator,
)
# 指数池定义在 config/universe.py（接入层+下游共用的单一事实源）。
# 接入层用 ALL_INDEX_CODES（含双版冗余，保证任何年份成分不漏）；
# 下游去重用 config.universe.CANONICAL_INDEX_CODES + CODE_TO_CANONICAL。
from config.universe import ALL_INDEX_CODES as INDEX_CODES

logger = logging.getLogger(__name__)


class _IndexByRangeMixin:
    """指数类接入层混入：遍历 INDEX_CODES，每个指数按区间一次取数（高效）。

    用于 index_daily / index_dailybasic —— 这两个接口支持 ts_code + start_date/
    end_date 区间，一个指数一次拿整段历史（fetch_tushare 自动分页）。

    覆盖 get_data（区间路径，回补/增量主路径）：遍历 18 个指数 × 区间
      → 1 年约 18 次 API 调用（每指数 1 次，含分页）。
    对比旧实现 fetch_one_period 逐交易日 × 逐指数单条取：1 年约 240×18=4320 次、
    每次只返回 1 行 → 浪费 ~99% API。数据结果完全一致。

    保留 fetch_one_period（单日 × 遍历指数）作兜底，供未走 get_data 的场景。
    """

    def get_data(self, start_date=None, end_date=None, **params):
        import time as _time

        if not start_date or not end_date:
            self.logger.warning(
                f"{self.table_name}.get_data 需要 start_date 和 end_date"
            )
            return pd.DataFrame()

        self.logger.info(
            f"{self.table_name}.get_data 按指数×区间取数：{len(INDEX_CODES)} 个指数 "
            f"[{start_date}, {end_date}]"
        )
        frames = []
        for i, code in enumerate(INDEX_CODES):
            try:
                df = self.fetch_tushare(
                    ts_code=code, start_date=start_date, end_date=end_date, **params
                )
            except Exception as e:
                self.logger.warning(
                    f"{self.table_name}.fetch_tushare(ts_code={code}) 失败: {e}"
                )
                continue
            if df is not None and len(df) > 0:
                frames.append(df)
            if (i + 1) % 5 == 0:
                _time.sleep(0.3)  # 防 tushare 限频

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        self.logger.info(f"{self.table_name}.get_data 完成，共 {len(combined)} 行")
        return combined

    def fetch_one_period(self, trade_date: str, **params):
        # 兜底：单日 × 遍历指数（增量单日等场景，主路径走 get_data 区间）
        frames = []
        for code in INDEX_CODES:
            df = self.fetch_tushare(ts_code=code, trade_date=trade_date, **params)
            if df is not None and not df.empty:
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else None


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
    type_overrides = {"desc": "TEXT"}  # 指数描述常超 255 字符，用 TEXT


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


class MoneyflowHsgtCalculator(TushareByTradeDateCalculator):
    """沪深港通资金流向（北向/南向，每日 1 行）。"""
    config_key = "moneyflow_hsgt"
    table_name = "moneyflow_hsgt"
    primary_keys = ["trade_date"]


class MarginCalculator(TushareByTradeDateCalculator):
    """融资融券交易汇总（每日 3 行：SSE/SZSE/BSE，主键必须含 exchange_id）。"""
    config_key = "margin"
    table_name = "margin"
    primary_keys = ["trade_date", "exchange_id"]


class LimitListDCalculator(TushareByTradeDateCalculator):
    """每日涨跌停/炸板（每只一行，主键必须含 ts_code；limit_type 留空一次取全 U/D/Z）。"""
    config_key = "limit_list_d"
    table_name = "limit_list_d"
    primary_keys = ["trade_date", "ts_code"]


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


class IndexDailyCalculator(_IndexByRangeMixin, TushareByTradeDateCalculator):
    """指数日线行情（遍历 index_codes × 区间取数）。"""
    config_key = "index_daily"
    table_name = "index_daily"
    primary_keys = ["ts_code", "trade_date"]


class IndexDailyBasicCalculator(_IndexByRangeMixin, TushareByTradeDateCalculator):
    """指数每日指标（遍历 index_codes × 区间取数）。"""
    config_key = "index_dailybasic"
    table_name = "index_daily_basic"
    primary_keys = ["ts_code", "trade_date"]


class IndexWeightCalculator(TushareByTradeDateCalculator):
    """指数成分股权重（月频：每月最后交易日取一次，遍历 index_codes）。

    index_weight 是月度数据。覆盖 get_data 只对「区间内每月最后交易日」调
    fetch_one_period（内部查 [月初, 月末] 整月区间），避免逐交易日重复拉取
    同月数据（旧实现 23 个交易日重复拉 23 次 → 13200 行重复 + 浪费 95% API）。
    """
    config_key = "index_weight"
    table_name = "index_weight"
    primary_keys = ["index_code", "con_code", "trade_date"]

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params
    ) -> pd.DataFrame:
        import time as _time

        from core.dates import get_trade_dates_between

        if not start_date or not end_date:
            self.logger.warning(
                f"{self.table_name}.get_data 需要 start_date 和 end_date"
            )
            return pd.DataFrame()

        trade_dates = get_trade_dates_between(start_date, end_date)
        if not trade_dates:
            self.logger.info(
                f"{self.table_name}.get_data 区间 [{start_date}, {end_date}] 无交易日"
            )
            return pd.DataFrame()

        # 每月最后交易日（按 yyyymm 分组，升序遍历后者覆盖 = 该月最后一个交易日）
        month_last = {}
        for td in sorted(trade_dates):
            month_last[td[:6]] = td
        month_ends = sorted(month_last.values())
        self.logger.info(
            f"{self.table_name}.get_data 月频取数：{len(trade_dates)} 个交易日 "
            f"→ {len(month_ends)} 个月末（{month_ends[0]}~{month_ends[-1]}）"
        )

        frames = []
        for i, td in enumerate(month_ends):
            try:
                df = self.fetch_one_period(trade_date=td, **params)
            except Exception as e:
                self.logger.warning(
                    f"{self.table_name}.fetch_one_period(month_end={td}) 失败: {e}"
                )
                continue
            if df is not None and len(df) > 0:
                frames.append(df)
            if (i + 1) % 5 == 0:
                _time.sleep(0.3)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        self.logger.info(f"{self.table_name}.get_data 完成，共 {len(combined)} 行")
        return combined

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        # 月初 = trade_date 当月 1 号；查 [月初, 月末] 整月区间
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


# ==================== by_period/by_ex_date: 财务三表 + 分红 + 披露（5 个） ====================

class IncomeCalculator(TushareByPeriodCalculator):
    """利润表（按报告期 period 取数 + overwrite 覆盖）。

    PK 用 5 列联合（实测 25 万行 0 重复）。约束：tushare income_vip 默认只返回
    report_type=1（合并报表）；若改拉其他 report_type，必须把 report_type 加进 PK。
    """
    config_key = "income_vip"
    table_name = "income"
    primary_keys = ["ts_code", "end_date", "ann_date", "f_ann_date", "update_flag"]
    write_mode = "overwrite"
    partition_col = "end_date"


class BalancesheetCalculator(TushareByPeriodCalculator):
    """资产负债表（按报告期 period 取数 + overwrite 覆盖）。"""
    config_key = "balancesheet_vip"
    table_name = "balancesheet"
    primary_keys = ["ts_code", "end_date", "ann_date", "f_ann_date", "update_flag"]
    write_mode = "overwrite"
    partition_col = "end_date"


class CashflowCalculator(TushareByPeriodCalculator):
    """现金流量表（按报告期 period 取数 + overwrite 覆盖）。"""
    config_key = "cashflow_vip"
    table_name = "cashflow"
    primary_keys = ["ts_code", "end_date", "ann_date", "f_ann_date", "update_flag"]
    write_mode = "overwrite"
    partition_col = "end_date"


class DividendCalculator(TushareByExDateCalculator):
    """分红送股（按除权除息日 ex_date 取数 + overwrite 覆盖）。

    只关心真实分红：ex_date 非空的"实施"记录才被命中，自动过滤预案/股东大会通过。
    旧实现逐 ann_date 自然日（365 次/年）+ 漏 ann_date=null；新实现按 ex_date 逐
    交易日拉全市场，配合 overwrite(partition_col=ex_date) 幂等。

    主键用 ex_date 不用 ann_date：实施记录的 ann_date 在远古(1990s)及个别记录为
    null，而 ann_date 是主键会违反 NOT NULL；ex_date 在实施记录里必非空、且是分红
    核心维度。ann_date 降为普通列保留真实值（含 null）。
    """
    config_key = "dividend"
    table_name = "dividend"
    primary_keys = ["ts_code", "end_date", "ex_date", "div_proc", "update_flag"]
    write_mode = "overwrite"
    partition_col = "ex_date"


class DisclosureDateCalculator(TushareByPeriodCalculator):
    """财报披露日期（按报告期 end_date 取数 + overwrite 覆盖）。

    官方文档：disclosure_date 取数参数是 end_date(报告期)，不支持 ann_date 区间。
    旧实现用 ann_date 逐日调，会漏掉所有 ann_date=null 的记录（早期/部分新股）；
    改按 end_date(报告期) 拉一次得全市场该期完整数据，无遗漏。

    继承 ByPeriodCalculator（内部按 period 拆分），覆盖 fetch_one_period 把内部
    period 映射到 tushare 的 end_date 参数。
    """
    config_key = "disclosure_date"
    table_name = "disclosure_date"
    primary_keys = ["ts_code", "end_date"]
    write_mode = "overwrite"
    partition_col = "end_date"

    def fetch_one_period(self, period: str, **params) -> Optional[pd.DataFrame]:
        # period 即报告期，tushare disclosure_date 用 end_date 参数接收
        return self.fetch_tushare(end_date=period, **params)


# ==================== fund: 场内基金/ETF（6 个） ====================

class FundBasicCalculator(TushareFullRefreshCalculator):
    """基金基本信息（场内 ETF/LOF，market=E，全量刷新）。"""
    config_key = "fund_basic"
    table_name = "fund_basic"
    primary_keys = ["ts_code"]


class FundDailyCalculator(TushareByTradeDateCalculator):
    """场内基金日线行情（逐交易日拉全市场）。"""
    config_key = "fund_daily"
    table_name = "fund_daily"
    primary_keys = ["ts_code", "trade_date"]


class FundAdjCalculator(TushareByTradeDateCalculator):
    """基金复权因子（逐交易日拉全市场）。"""
    config_key = "fund_adj"
    table_name = "fund_adj"
    primary_keys = ["ts_code", "trade_date"]


class FundShareCalculator(TushareByTradeDateCalculator):
    """基金规模/每日份额（逐交易日拉全市场，含场内场外）。"""
    config_key = "fund_share"
    table_name = "fund_share"
    primary_keys = ["ts_code", "trade_date"]


class FundFactorProCalculator(TushareByTradeDateCalculator):
    """场内基金技术面因子（60+ 指标：MA/MACD/RSI/Boll/KDJ/ATR 等）。
    
    Tushare 自产数据，覆盖全历史。逐交易日拉全市场。
    积分要求：5000。
    """
    config_key = "fund_factor_pro"
    table_name = "fund_factor_pro"
    primary_keys = ["ts_code", "trade_date"]


class FundNavCalculator(TushareByTradeDateCalculator):
    """公募基金净值（日频 unit_nav/accum_nav/adj_nav）。
    
    biz_date_col='nav_date'（非 trade_date），数据按净值日期组织。
    fetch_one_period 内部把 base 传进来的 trade_date 参数映射到 tushare 的 nav_date。
    用途：ETF 折溢价率 = fund_daily.close / fund_nav.unit_nav - 1。
    积分要求：2000。
    """
    config_key = "fund_nav"
    table_name = "fund_nav"
    primary_keys = ["ts_code", "nav_date"]
    write_mode = "overwrite"
    partition_col = "nav_date"  # 覆盖基类的 trade_date，用净值日期分区

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        # by_trade_date 策略按交易日调度，但 tushare fund_nav 接口的参数是 nav_date
        return self.fetch_tushare(nav_date=trade_date, **params)


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
    "moneyflow_hsgt": MoneyflowHsgtCalculator,
    "margin": MarginCalculator,
    "limit_list_d": LimitListDCalculator,
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
    "fund_basic": FundBasicCalculator,
    "fund_daily": FundDailyCalculator,
    "fund_adj": FundAdjCalculator,
    "fund_share": FundShareCalculator,
    "fund_factor_pro": FundFactorProCalculator,
    "fund_nav": FundNavCalculator,
}
