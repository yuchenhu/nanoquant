"""个股×日 历史百分位 Panel（从 data/sql/stock_percentiles.py 迁移）。

表名：panel_stock_percentiles
主键：ts_code + trade_date
biz_date_col：trade_date
依赖：stock_daily_panel（panel_stock_daily）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from core.dates import get_previous_n_trading_date
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class StockPercentilesCalculator(PanelCalculator):
    """个股×日 历史百分位（价格/PE/PE_TTM/PB 的 1 年 tsrank + 均线 + 波动率）。"""

    table_name = "stock_percentiles"  # → panel_stock_percentiles
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "close": "float", "pe": "float", "pe_ttm": "float", "pb": "float",
        "turnover_rate": "float", "pct_chg": "float",
        "price_tsrank_1y": "float", "pe_tsrank_1y": "float",
        "pe_ttm_tsrank_1y": "float", "pb_tsrank_1y": "float",
        "ma20": "float", "ma60": "float", "ma250": "float",
        "volatility_20": "float", "volatility_60": "float", "volatility_250": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.lookback_1y = 250
        self.min_history_days = 120
        self.logger.info("StockPercentilesCalculator 初始化")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """取 panel_stock_daily（扩展前 350 天以保证有足够历史算百分位）。"""
        extended_start = None
        if start_date:
            sd = start_date.replace('-', '')
            extended_start = get_previous_n_trading_date(sd, 250 + 100)
        query = """
        SELECT ts_code, trade_date, close, pre_close, pct_chg,
               pe, pe_ttm, pb, turnover_rate, adj_factor
        FROM panel_stock_daily WHERE 1=1
        """
        if extended_start:
            query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            ed = end_date.replace('-', '') if isinstance(end_date, str) else end_date
            query += f" AND trade_date <= '{ed}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"
        query += " ORDER BY ts_code, trade_date"
        self.logger.info(
            f"取 panel_stock_daily: {extended_start or '开始'}~{end_date or '结束'}, "
            f"股票数: {len(entity_list) if entity_list else '全部'}"
        )
        return pd.read_sql(query, self.engine)

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return data
        start_date = params.get("start_date")
        end_date = params.get("end_date")
        self.logger.info(f"开始处理百分位，输入 {len(data)} 条")

        results = []
        for ts_code, group in data.groupby('ts_code'):
            try:
                stock_result = self._process_single_stock(group)
                if start_date and end_date:
                    stock_result['trade_date_str'] = stock_result['trade_date'].astype(str).str.replace('-', '')
                    stock_result = stock_result[stock_result['trade_date_str'].between(start_date, end_date)]
                    stock_result = stock_result.drop('trade_date_str', axis=1)
                if not stock_result.empty:
                    results.append(stock_result)
            except Exception as e:
                self.logger.error(f"处理 {ts_code} 出错: {e}")

        if not results:
            return pd.DataFrame()
        final = pd.concat(results, ignore_index=True)
        final = final.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        self.logger.info(f"百分位处理完成: {len(final)} 条")
        return final

    def _process_single_stock(self, stock_data: pd.DataFrame) -> pd.DataFrame:
        stock_data = stock_data.sort_values('trade_date').copy()
        stock_data['adj_close'] = stock_data['close'] * stock_data['adj_factor']
        stock_data = self._calculate_technical_indicators(stock_data)
        stock_data = self._calculate_rolling_percentiles(stock_data)
        cols = [
            'ts_code', 'trade_date', 'close', 'pe', 'pe_ttm', 'pb',
            'turnover_rate', 'pct_chg',
            'price_tsrank_1y', 'pe_tsrank_1y', 'pe_ttm_tsrank_1y', 'pb_tsrank_1y',
            'ma20', 'ma60', 'ma250',
            'volatility_20', 'volatility_60', 'volatility_250',
        ]
        available = [c for c in cols if c in stock_data.columns]
        return stock_data[available]

    def _calculate_technical_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()
        data['ma250'] = data['close'].rolling(250).mean()
        data['volatility_20'] = data['pct_chg'].rolling(20).std()
        data['volatility_60'] = data['pct_chg'].rolling(60).std()
        data['volatility_250'] = data['pct_chg'].rolling(250).std()
        return data

    def _calculate_rolling_percentiles(self, data: pd.DataFrame) -> pd.DataFrame:
        metrics = [('price', 'adj_close'), ('pe', 'pe'), ('pe_ttm', 'pe_ttm'), ('pb', 'pb')]
        for name, src in metrics:
            if src not in data.columns:
                continue
            data[f'{name}_tsrank_1y'] = data[src].rolling(
                window=self.lookback_1y, min_periods=self.min_history_days
            ).apply(lambda x: self._percentile_in_window(x), raw=True)
        return data

    def _percentile_in_window(self, window_values):
        if len(window_values) < self.min_history_days or np.isnan(window_values[-1]):
            return np.nan
        try:
            current = window_values[-1]
            historical = window_values[:-1]
            historical = historical[~np.isnan(historical)]
            if len(historical) < self.min_history_days:
                return np.nan
            return stats.percentileofscore(historical, current, kind='mean') / 100.0
        except Exception:
            return np.nan
