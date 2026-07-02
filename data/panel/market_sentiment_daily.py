"""市场x日 情绪/状态底表（占位，待实现）。

表名：panel_market_sentiment_daily
主键：trade_date
biz_date_col：trade_date
write_mode：upsert
依赖：panel_stock_daily

TODO: 日频市场状态指标（涨跌比、量比、波动率等）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MarketSentimentDailyCalculator(PanelCalculator):
    """市场x日 情绪（占位，待实现）。"""

    table_name = "market_sentiment_daily"
    primary_keys = ["trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "trade_date": "string",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MarketSentimentDailyCalculator（占位）初始化")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        return pd.DataFrame()
