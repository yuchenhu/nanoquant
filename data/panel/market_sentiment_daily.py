"""市场×日 情绪 Panel（从 data/sql/market_sentiment_daily.py 迁移）。

表名：panel_market_sentiment_daily
主键：trade_date + dimension_type + dimension_value
biz_date_col：trade_date
依赖：stock_daily_panel + stock_percentiles
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MarketSentimentDailyCalculator(PanelCalculator):
    """市场×日 情绪（按市值/指数/行业三维度聚合涨跌分布+主力资金+估值+百分位+均线）。"""

    table_name = "market_sentiment_daily"  # → panel_market_sentiment_daily
    primary_keys = ["trade_date", "dimension_type", "dimension_value"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "trade_date": "string", "dimension_type": "string", "dimension_value": "string",
        "stock_count": "int", "valid_count": "int",
        "up_ratio": "float", "strong_up_ratio": "float", "down_ratio": "float",
        "strong_down_ratio": "float", "flat_ratio": "float",
        "avg_pct_chg": "float", "avg_up_pct_chg": "float", "avg_down_pct_chg": "float",
        "avg_turnover_rate": "float", "total_amount": "float", "avg_amount": "float",
        "main_buy_amount": "float", "main_sell_amount": "float",
        "main_net_inflow": "float", "main_net_inflow_ratio": "float",
        "avg_pe": "float", "avg_pe_ttm": "float", "avg_pb": "float",
        "avg_price_tsrank_1y": "float", "avg_pe_tsrank_1y": "float",
        "avg_pe_ttm_tsrank_1y": "float", "avg_pb_tsrank_1y": "float",
        "avg_volatility_20": "float", "avg_volatility_60": "float", "avg_volatility_250": "float",
        "above_ma20_ratio": "float", "below_ma20_ratio": "float",
        "above_ma60_ratio": "float", "below_ma60_ratio": "float",
        "above_ma250_ratio": "float", "below_ma250_ratio": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MarketSentimentDailyCalculator 初始化")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        stock_list: Optional[List[str]] = params.get("stock_list") or params.get("entity_list")
        query = """
        SELECT
            w.ts_code, w.trade_date, w.pct_chg, w.turnover_rate, w.amount,
            w.pe, w.pe_ttm, w.pb, w.total_mv,
            w.buy_lg_amount, w.buy_elg_amount, w.sell_lg_amount, w.sell_elg_amount,
            w.l1_name,
            w.is_hs300, w.is_zz500, w.is_zz1000, w.is_zz2000,
            CASE
                WHEN w.is_hs300 = 1 THEN '1.沪深300'
                WHEN w.is_zz500 = 1 THEN '2.中证500'
                WHEN w.is_zz1000 = 1 THEN '3.中证1000'
                WHEN w.is_zz2000 = 1 THEN '4.中证2000'
                ELSE '5.其他'
            END AS index_category,
            p.price_tsrank_1y, p.pe_tsrank_1y, p.pe_ttm_tsrank_1y, p.pb_tsrank_1y,
            p.ma20, p.ma60, p.ma250,
            p.volatility_20, p.volatility_60, p.volatility_250,
            w.close
        FROM (
            SELECT * FROM panel_stock_daily WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        if stock_list:
            codes_str = ",".join([f"'{c}'" for c in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        query += """
        ) w
        LEFT JOIN (
            SELECT * FROM panel_stock_percentiles WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        if stock_list:
            codes_str = ",".join([f"'{c}'" for c in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        query += """
        ) p ON w.ts_code = p.ts_code AND w.trade_date = p.trade_date
        ORDER BY w.trade_date, w.ts_code
        """
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            self.logger.error(f"获取数据失败: {e}")
            return pd.DataFrame()

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return pd.DataFrame()
        start_date = params.get("start_date")
        end_date = params.get("end_date")
        self.logger.info(f"开始计算市场热度，输入 {len(data)} 条")

        df = self._preprocess_data(data.copy())
        df = self._add_dimension_labels(df)

        dimensions = [('cap', 'cap_category'), ('index', 'index_category'), ('industry', 'l1_name')]
        results = []
        for dim_type, dim_col in dimensions:
            r = self._calculate_dimension_sentiment(df, dim_type, dim_col)
            if not r.empty:
                results.append(r)

        if not results:
            return pd.DataFrame()
        final = pd.concat(results, ignore_index=True)
        if start_date and end_date:
            final['trade_date_str'] = final['trade_date'].astype(str).str.replace('-', '')
            final = final[final['trade_date_str'].between(start_date, end_date)]
            final = final.drop('trade_date_str', axis=1)
        final = final.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        self.logger.info(f"市场热度完成: {len(final)} 条")
        return final

    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
        df['amount'] = df['amount'] / 10000  # 万元 → 亿元
        df['main_buy_amount'] = (df['buy_lg_amount'] + df['buy_elg_amount']).fillna(0) / 10000
        df['main_sell_amount'] = (df['sell_lg_amount'] + df['sell_elg_amount']).fillna(0) / 10000
        df['main_net_inflow'] = df['main_buy_amount'] - df['main_sell_amount']
        return df

    def _add_dimension_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        conditions = [
            df['total_mv'] < 200000,
            (df['total_mv'] >= 200000) & (df['total_mv'] < 500000),
            (df['total_mv'] >= 500000) & (df['total_mv'] < 1000000),
            (df['total_mv'] >= 1000000) & (df['total_mv'] < 3000000),
            df['total_mv'] >= 3000000,
        ]
        choices = ['1.<20亿', '2.20-50亿', '3.50-100亿', '4.100-300亿', '5.>=300亿']
        df['cap_category'] = np.select(conditions, choices, default='0.未知')
        df['l1_name'] = df['l1_name'].fillna('未知行业')
        return df

    def _calculate_dimension_sentiment(self, df: pd.DataFrame, dim_type: str, dim_col: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        results = []
        for (trade_date, dim_value), group in df.groupby(['trade_date', dim_col]):
            if len(group) < 3:
                continue
            s = self._calculate_single_group_sentiment(group, trade_date, dim_type, str(dim_value))
            if s:
                results.append(s)
        return pd.DataFrame(results) if results else pd.DataFrame()

    def _calculate_single_group_sentiment(
        self, group: pd.DataFrame, trade_date: str, dim_type: str, dim_value: str
    ) -> Optional[Dict]:
        try:
            result = {
                'trade_date': trade_date, 'dimension_type': dim_type, 'dimension_value': dim_value,
                'stock_count': len(group), 'valid_count': group['pct_chg'].notna().sum(),
            }
            if result['valid_count'] == 0:
                return None
            pct_chg = group['pct_chg'].dropna()
            up_mask = pct_chg > 0.5
            strong_up_mask = pct_chg > 5
            down_mask = pct_chg < -0.5
            strong_down_mask = pct_chg < -5
            flat_mask = (pct_chg >= -0.5) & (pct_chg <= 0.5)
            result['up_ratio'] = round(up_mask.mean(), 4)
            result['strong_up_ratio'] = round(strong_up_mask.mean(), 4)
            result['down_ratio'] = round(down_mask.mean(), 4)
            result['strong_down_ratio'] = round(strong_down_mask.mean(), 4)
            result['flat_ratio'] = round(flat_mask.mean(), 4)
            result['avg_pct_chg'] = round(pct_chg.mean(), 4)
            up_pct = pct_chg[up_mask]
            down_pct = pct_chg[down_mask]
            result['avg_up_pct_chg'] = round(up_pct.mean(), 4) if not up_pct.empty else 0.0
            result['avg_down_pct_chg'] = round(down_pct.mean(), 4) if not down_pct.empty else 0.0
            turnover = group['turnover_rate'].dropna()
            if not turnover.empty:
                result['avg_turnover_rate'] = round(turnover.mean(), 4)
            amount = group['amount'].dropna()
            if not amount.empty:
                result['total_amount'] = round(amount.sum(), 4)
                result['avg_amount'] = round(amount.mean(), 4)
            if all(c in group.columns for c in ['main_buy_amount', 'main_sell_amount', 'main_net_inflow']):
                main_buy = group['main_buy_amount'].dropna()
                main_sell = group['main_sell_amount'].dropna()
                net_inflow = group['main_net_inflow'].dropna()
                if not main_buy.empty:
                    result['main_buy_amount'] = round(main_buy.sum(), 4)
                if not main_sell.empty:
                    result['main_sell_amount'] = round(main_sell.sum(), 4)
                if not net_inflow.empty and amount.sum() > 0:
                    result['main_net_inflow'] = round(net_inflow.sum(), 4)
                    result['main_net_inflow_ratio'] = round(net_inflow.sum() / amount.sum(), 4)
            for pe_col, target_col in [('pe', 'avg_pe'), ('pe_ttm', 'avg_pe_ttm'), ('pb', 'avg_pb')]:
                if pe_col in group.columns:
                    values = group[pe_col].replace([np.inf, -np.inf], np.nan).dropna()
                    values = values[(values > 0) & (values < 1000)]
                    if not values.empty:
                        result[target_col] = round(values.mean(), 4)
            percentile_cols = {
                'price_tsrank_1y': 'avg_price_tsrank_1y', 'pe_tsrank_1y': 'avg_pe_tsrank_1y',
                'pe_ttm_tsrank_1y': 'avg_pe_ttm_tsrank_1y', 'pb_tsrank_1y': 'avg_pb_tsrank_1y',
            }
            for src, tgt in percentile_cols.items():
                if src in group.columns:
                    values = group[src].dropna()
                    values = values[(values >= 0) & (values <= 1)]
                    if not values.empty:
                        result[tgt] = round(values.mean(), 4)
            vol_cols = {'volatility_20': 'avg_volatility_20', 'volatility_60': 'avg_volatility_60', 'volatility_250': 'avg_volatility_250'}
            for src, tgt in vol_cols.items():
                if src in group.columns:
                    values = group[src].dropna()
                    values = values[values >= 0]
                    if not values.empty:
                        result[tgt] = round(values.mean(), 4)
            if all(c in group.columns for c in ['close', 'ma20', 'ma60', 'ma250']):
                valid = group[['close', 'ma20', 'ma60', 'ma250']].dropna()
                if not valid.empty:
                    above_ma20 = (valid['close'] > valid['ma20']).mean()
                    above_ma60 = (valid['close'] > valid['ma60']).mean()
                    above_ma250 = (valid['close'] > valid['ma250']).mean()
                    result.update({
                        'above_ma20_ratio': round(above_ma20, 4), 'below_ma20_ratio': round(1 - above_ma20, 4),
                        'above_ma60_ratio': round(above_ma60, 4), 'below_ma60_ratio': round(1 - above_ma60, 4),
                        'above_ma250_ratio': round(above_ma250, 4), 'below_ma250_ratio': round(1 - above_ma250, 4),
                    })
            return result
        except Exception as e:
            self.logger.warning(f"计算分组热度失败: {e}")
            return None
