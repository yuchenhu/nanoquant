"""个股×月 市值快照 Panel（从 data/sql/mv_monthly.py 迁移）。

表名：panel_mv_monthly
主键：ts_code + trade_date（月末最后交易日）
biz_date_col：trade_date
依赖：stock_daily_basic
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MvMonthlyCalculator(PanelCalculator):
    """个股×月 市值快照（每月最后交易日的 total_mv/circ_mv/total_share/float_share）。"""

    table_name = "mv_monthly"  # → panel_mv_monthly
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "total_mv": "float", "circ_mv": "float",
        "total_share": "float", "float_share": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MvMonthlyCalculator 初始化")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        query = """
        SELECT ts_code, trade_date, total_mv, circ_mv, total_share, float_share,
        ROW_NUMBER() OVER (PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m') ORDER BY trade_date DESC) as rn
        FROM stock_daily_basic WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"
        query += " ORDER BY ts_code, trade_date"
        return pd.read_sql(query, self.engine)

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return data
        result = data[data.rn == 1].drop('rn', axis=1)
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        return result
