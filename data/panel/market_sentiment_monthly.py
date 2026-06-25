"""市场x月 情绪/状态 Panel（regime 底表，第一性原理重设计）。

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
biz_date_col：trade_date（月末交易日）
write_mode：upsert

================================ 设计原则 ================================
1. 这是 regime 的"原始指标底表"：只存 level + 原始分量，不做标准化/趋势/合成
   （那些是上层 factor_regime_features / factor_regime_score 的活）。
2. 衍生指标必须同表存放其原始分量（如 ma_bull_align 必带 ma60/ma120/ma250）。
3. 无未来函数：所有列都是截至本月末"当时可知"的回看窗口统计。
4. 维度（dimension_type / dimension_value）：
   - 'all'   全A          —— 全局 regime + 全市场独有指标（北向/两融/涨停家数）
   - 'index' 上证50 / 沪深300 / 中证500 / 中证1000 / 中证2000 —— 风格维度 regime
   说明：北向/两融/涨停为全市场口径，仅在 'all' 行有值，'index' 行为 NULL。
        成分分布/估值/离散度按各指数成分计算，'all' 行按全A计算。

================================ 上游来源 ================================
追溯到接入层（不假设中间 panel 存在；待个股 panel 就绪后回填实现）：
- 指数自身：index_daily / index_dailybasic
- 成分归属：panel_index_membership_monthly（清洗后的月末成分，来自 index_weight）
- 成分分布/估值/离散度：daily / daily_basic（+ 成分归属过滤）
- 资金：moneyflow（主力）/ moneyflow_hsgt（北向）/ margin（两融）
- 情绪：limit_list_d（涨跌停，数据始于2020）
- ERP 国债收益率：手工维护的月度小表（绕过无权限的 yc_cb）

实现状态：本版仅钉死 output_schema；get_data/process_data 为 TODO 占位，
        返回空（update 会优雅跳过，不阻塞 pipeline）。待个股 panel 就绪后实现。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)

# 本表覆盖的指数维度（dimension_value）。全A 用 'all' 维度单独一行。
INDEX_DIMENSIONS = ["上证50", "沪深300", "中证500", "中证1000", "中证2000"]


class MarketSentimentMonthlyCalculator(PanelCalculator):
    """市场x月 情绪/状态 底表（6 支柱核心指标，index 5 指数 + all 全A）。"""

    table_name = "market_sentiment_monthly"  # -> panel_market_sentiment_monthly
    primary_keys = ["trade_date", "dimension_type", "dimension_value"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # output_schema 钉死：列按 6 支柱分组。RAW=原始; DER=派生(同表带其原始分量)。
    output_schema = {
        # ---- 维度键 + 元信息 ----
        "trade_date": "string",        # 月末交易日 YYYYMMDD
        "dimension_type": "string",    # 'all' / 'index'
        "dimension_value": "string",   # '全A' / 上证50 / 沪深300 / ...
        "stock_count": "int",          # 该维度成分数量
        "valid_count": "int",          # 有效样本数

        # ===== 支柱1 趋势（指数自身价格行为）=====
        "idx_close": "float",          # RAW 指数月末收盘
        "ma60": "float",               # RAW 季线
        "ma120": "float",              # RAW 半年线
        "ma250": "float",              # RAW 年线（牛熊分水岭）
        "ma_bull_align": "int",        # DER 多头排列(ma60>ma120>ma250) 0/1 [带 ma60/120/250]
        "idx_ret_3m": "float",         # RAW 近3月指数收益
        "idx_ret_6m": "float",         # RAW 近6月指数收益

        # ===== 支柱2 广度（成分股分布，辨真假牛）=====
        "up_count": "int",            # RAW 本月上涨成分家数
        "down_count": "int",          # RAW 本月下跌成分家数
        "big_up_count": "int",        # RAW 本月涨>10% 家数
        "big_down_count": "int",      # RAW 本月跌<-10% 家数
        "profit_ratio": "float",      # DER 赚钱效应=正收益占比 [带 up/down_count]
        "pct_above_ma250": "float",   # DER 成分站上年线占比
        "limit_up_count": "int",      # RAW 涨停家数（仅 'all'，来自 limit_list_d）

        # ===== 支柱3 量能 =====
        "idx_amount": "float",        # RAW 指数月度成交额
        "amount_pct_1y": "float",     # DER 成交额1年分位 [带 idx_amount]

        # ===== 支柱4 资金（全市场口径，仅 'all'）=====
        "north_money": "float",          # RAW 北向净流入（moneyflow_hsgt）
        "margin_balance": "float",       # RAW 两融余额（margin rzrqye）
        "main_net_inflow_ratio": "float",# DER 主力净流入占比（moneyflow）

        # ===== 支柱5 估值（跨周期高低估锚）=====
        "pe_ttm_median": "float",     # RAW 成分 PE_TTM 中位数
        "pb_median": "float",         # RAW 成分 PB 中位数
        "pe_pct_5y": "float",         # DER PE 5年分位 [带 pe_ttm_median]
        "pb_pct_5y": "float",         # DER PB 5年分位 [带 pb_median]
        "erp": "float",               # DER 股债性价比=1/PE_TTM - 10Y国债 [带 pe_ttm_median]

        # ===== 支柱6 波动/风险（辨趋势 vs 震荡）=====
        "idx_volatility_60": "float",      # RAW 指数60日年化波动
        "avg_correlation": "float",        # DER 成分平均两两相关（飙升=系统性同涨同跌）
        "max_drawdown_1y": "float",        # DER 指数滚动1年最大回撤
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MarketSentimentMonthlyCalculator 初始化（schema 已钉死，实现待个股 panel）")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        # TODO(impl): 待个股 panel + panel_index_membership_monthly 就绪后实现。
        # 取数计划：
        #   1. index_daily/index_dailybasic 取 5 指数 + 全A 基准的月末行情 -> 趋势/量能/波动
        #   2. panel_index_membership_monthly 取月末成分归属 -> 过滤成分
        #   3. daily/daily_basic 按成分聚合 -> 广度/估值/离散度
        #   4. moneyflow_hsgt / margin / moneyflow / limit_list_d -> 资金/情绪（仅 'all'）
        self.logger.warning(
            "market_sentiment_monthly.get_data 未实现（schema 占位中），返回空"
        )
        return pd.DataFrame()

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        # TODO(impl): 按 6 支柱计算各列；'all' 与 'index' 两类维度分别聚合。
        # 衍生列必须与其原始分量一同输出（见 output_schema 注释 [带 ...]）。
        if data is None or data.empty:
            return pd.DataFrame()
        return pd.DataFrame()
