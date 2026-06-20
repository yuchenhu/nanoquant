"""市场×月 情绪 Panel（从 data/sql/market_sentiment_monthly.py 迁移）。

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
biz_date_col：trade_date（月末自然日 / 最新月用最后交易日）
依赖：market_sentiment_daily（panel_market_sentiment_daily 的上游 panel_stock_daily + panel_stock_percentiles）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MarketSentimentMonthlyCalculator(PanelCalculator):
    """市场×月 情绪（月度聚合：涨跌分布+主力资金+估值+百分位+均线，三维度）。"""

    table_name = "market_sentiment_monthly"  # → panel_market_sentiment_monthly
    primary_keys = ["trade_date", "dimension_type", "dimension_value"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "trade_date": "string", "dimension_type": "string", "dimension_value": "string",
        "stock_count": "int", "valid_count": "int",
        "up_ratio": "float", "strong_up_ratio": "float", "down_ratio": "float", "strong_down_ratio": "float",
        "avg_pct_chg": "float", "avg_up_pct_chg": "float", "avg_down_pct_chg": "float",
        "avg_turnover_rate": "float", "total_amount": "float", "avg_amount": "float",
        "total_volume": "float", "avg_volume": "float",
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
        self.logger.info("MarketSentimentMonthlyCalculator 初始化")

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        stock_list: Optional[List[str]] = params.get("stock_list") or params.get("entity_list")
        query = """
        WITH ranked_daily_data AS (
            SELECT ts_code, trade_date,
                DATE_FORMAT(trade_date, '%%Y-%%m-01') as month_start,
                open, high, low, close, amount, turnover_rate, vol, pct_chg,
                buy_lg_amount, buy_elg_amount, sell_lg_amount, sell_elg_amount,
                l1_name, total_mv, is_hs300, is_zz500, is_zz1000, is_zz2000,
                pe, pe_ttm, pb,
                ROW_NUMBER() OVER (PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01') ORDER BY trade_date) as first_day_rn,
                ROW_NUMBER() OVER (PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01') ORDER BY trade_date DESC) as last_day_rn
            FROM panel_stock_daily WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        if stock_list:
            codes_str = ",".join([f"'{c}'" for c in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        query += """
        ),
        ranked_percentiles_data AS (
            SELECT ts_code, trade_date,
                DATE_FORMAT(trade_date, '%%Y-%%m-01') as month_start,
                price_tsrank_1y, pe_tsrank_1y, pe_ttm_tsrank_1y, pb_tsrank_1y,
                ma20, ma60, ma250, close,
                volatility_20, volatility_60, volatility_250,
                ROW_NUMBER() OVER (PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01') ORDER BY trade_date DESC) as last_day_rn
            FROM panel_stock_percentiles WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        if stock_list:
            codes_str = ",".join([f"'{c}'" for c in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        query += """
        ),
        monthly_aggregates AS (
            SELECT ts_code, month_start,
                MAX(trade_date) as last_trade_date, MIN(trade_date) as first_trade_date,
                MAX(high) as month_high, MIN(low) as month_low,
                AVG(amount) as avg_daily_amount, AVG(turnover_rate) as avg_turnover_rate,
                AVG(vol) as avg_daily_volume,
                AVG(buy_lg_amount + buy_elg_amount) as avg_daily_main_buy,
                AVG(sell_lg_amount + sell_elg_amount) as avg_daily_main_sell
            FROM ranked_daily_data GROUP BY ts_code, month_start
        ),
        first_day_data AS (
            SELECT ts_code, month_start, open as month_open
            FROM ranked_daily_data WHERE first_day_rn = 1
        ),
        last_day_data AS (
            SELECT ts_code, month_start, close as month_close,
                l1_name as month_end_l1_name, total_mv as month_end_total_mv,
                is_hs300 as month_end_is_hs300, is_zz500 as month_end_is_zz500,
                is_zz1000 as month_end_is_zz1000, is_zz2000 as month_end_is_zz2000,
                pe as month_end_pe, pe_ttm as month_end_pe_ttm, pb as month_end_pb
            FROM ranked_daily_data WHERE last_day_rn = 1
        ),
        last_day_percentiles AS (
            SELECT ts_code, month_start,
                price_tsrank_1y, pe_tsrank_1y, pe_ttm_tsrank_1y, pb_tsrank_1y,
                ma20, ma60, ma250, close as month_end_close,
                volatility_20, volatility_60, volatility_250
            FROM ranked_percentiles_data WHERE last_day_rn = 1
        )
        SELECT ma.ts_code, ma.month_start,
            LAST_DAY(ma.month_start) as month_end_natural,
            ma.last_trade_date, ma.first_trade_date,
            ma.month_high, ma.month_low,
            ma.avg_daily_amount, ma.avg_turnover_rate, ma.avg_daily_volume,
            ma.avg_daily_main_buy, ma.avg_daily_main_sell,
            fd.month_open, ld.month_close, ld.month_end_l1_name, ld.month_end_total_mv,
            ld.month_end_is_hs300, ld.month_end_is_zz500, ld.month_end_is_zz1000, ld.month_end_is_zz2000,
            CASE
                WHEN ld.month_end_is_hs300 = 1 THEN '1.沪深300'
                WHEN ld.month_end_is_zz500 = 1 THEN '2.中证500'
                WHEN ld.month_end_is_zz1000 = 1 THEN '3.中证1000'
                WHEN ld.month_end_is_zz2000 = 1 THEN '4.中证2000'
                ELSE '5.其他'
            END AS index_category,
            ld.month_end_pe, ld.month_end_pe_ttm, ld.month_end_pb,
            lp.price_tsrank_1y, lp.pe_tsrank_1y, lp.pe_ttm_tsrank_1y, lp.pb_tsrank_1y,
            lp.ma20, lp.ma60, lp.ma250, lp.month_end_close,
            lp.volatility_20, lp.volatility_60, lp.volatility_250
        FROM monthly_aggregates ma
        LEFT JOIN first_day_data fd ON ma.ts_code = fd.ts_code AND ma.month_start = fd.month_start
        LEFT JOIN last_day_data ld ON ma.ts_code = ld.ts_code AND ma.month_start = ld.month_start
        LEFT JOIN last_day_percentiles lp ON ma.ts_code = lp.ts_code AND ma.month_start = lp.month_start
        ORDER BY ma.month_start, ma.ts_code
        """
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            self.logger.error(f"获取月度数据失败: {e}")
            return pd.DataFrame()

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return pd.DataFrame()
        self.logger.info(f"开始计算月度市场热度，输入 {len(data)} 条")
        df = self._preprocess_data(data)
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
        if not final.empty:
            max_td = final['last_trade_date'].max()
            latest_month = final[final['last_trade_date'] == max_td]['month_start'].iloc[0]
            final['trade_date'] = final.apply(
                lambda row: row['last_trade_date'] if row['month_start'] == latest_month else row['month_end_natural'],
                axis=1,
            )
        final = final.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        for col in ['month_start', 'month_end_natural', 'last_trade_date', 'first_trade_date']:
            if col in final.columns:
                final = final.drop(col, axis=1)
        if 'trade_date' in final.columns:
            cols = ['trade_date'] + [c for c in final.columns if c != 'trade_date']
            final = final[cols]
        self.logger.info(f"月度市场热度完成: {len(final)} 条")
        return final

    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
        df['monthly_pct_chg'] = ((df['month_close'] - df['month_open']) / df['month_open'] * 100).fillna(0)
        df['monthly_amplitude'] = ((df['month_high'] - df['month_low']) / df['month_open'] * 100).fillna(0)
        df['avg_daily_amount'] = df['avg_daily_amount'] / 10000
        df['avg_daily_main_buy'] = df['avg_daily_main_buy'] / 10000
        df['avg_daily_main_sell'] = df['avg_daily_main_sell'] / 10000
        df['avg_daily_main_net_inflow'] = df['avg_daily_main_buy'] - df['avg_daily_main_sell']
        return df

    def _add_dimension_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        conditions = [
            df['month_end_total_mv'] < 200000,
            (df['month_end_total_mv'] >= 200000) & (df['month_end_total_mv'] < 500000),
            (df['month_end_total_mv'] >= 500000) & (df['month_end_total_mv'] < 1000000),
            (df['month_end_total_mv'] >= 1000000) & (df['month_end_total_mv'] < 3000000),
            df['month_end_total_mv'] >= 3000000,
        ]
        choices = ['1.<20亿', '2.20-50亿', '3.50-100亿', '4.100-300亿', '5.>=300亿']
        df['cap_category'] = np.select(conditions, choices, default='0.未知')
        df['l1_name'] = df['month_end_l1_name'].fillna('未知行业')
        return df

    def _calculate_dimension_sentiment(self, df: pd.DataFrame, dim_type: str, dim_col: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        results = []
        for (month_start, dim_value), group in df.groupby(['month_start', dim_col]):
            if len(group) < 3:
                continue
            s = self._calculate_single_group_sentiment(group, month_start, dim_type, str(dim_value))
            if s:
                results.append(s)
        return pd.DataFrame(results) if results else pd.DataFrame()

    def _calculate_single_group_sentiment(
        self, group: pd.DataFrame, month_start: str, dim_type: str, dim_value: str
    ) -> Optional[Dict]:
        try:
            result = {
                'month_start': month_start, 'dimension_type': dim_type, 'dimension_value': dim_value,
                'stock_count': len(group), 'valid_count': group['monthly_pct_chg'].notna().sum(),
                'last_trade_date': group['last_trade_date'].max(),
                'month_end_natural': group['month_end_natural'].iloc[0],
            }
            if result['valid_count'] == 0:
                return None
            monthly_pct = group['monthly_pct_chg'].dropna()
            up_mask = monthly_pct > 0
            strong_up_mask = monthly_pct > 10
            down_mask = monthly_pct < 0
            strong_down_mask = monthly_pct < -10
            result['up_ratio'] = round(up_mask.mean(), 4)
            result['strong_up_ratio'] = round(strong_up_mask.mean(), 4)
            result['down_ratio'] = round(down_mask.mean(), 4)
            result['strong_down_ratio'] = round(strong_down_mask.mean(), 4)
            result['avg_pct_chg'] = round(monthly_pct.mean(), 4)
            up_pct = monthly_pct[up_mask]
            down_pct = monthly_pct[down_mask]
            result['avg_up_pct_chg'] = round(up_pct.mean(), 4) if not up_pct.empty else 0.0
            result['avg_down_pct_chg'] = round(down_pct.mean(), 4) if not down_pct.empty else 0.0
            turnover = group['avg_turnover_rate'].dropna()
            if not turnover.empty:
                result['avg_turnover_rate'] = round(turnover.mean(), 4)
            amount = group['avg_daily_amount'].dropna()
            if not amount.empty:
                result['total_amount'] = round(amount.sum(), 4)
                result['avg_amount'] = round(amount.mean(), 4)
            volume = group['avg_daily_volume'].dropna()
            if not volume.empty:
                result['total_volume'] = round(volume.sum(), 4)
                result['avg_volume'] = round(volume.mean(), 4)
            if all(c in group.columns for c in ['avg_daily_main_buy', 'avg_daily_main_sell', 'avg_daily_main_net_inflow']):
                main_buy = group['avg_daily_main_buy'].dropna()
                main_sell = group['avg_daily_main_sell'].dropna()
                net_inflow = group['avg_daily_main_net_inflow'].dropna()
                if not main_buy.empty:
                    result['main_buy_amount'] = round(main_buy.sum(), 4)
                if not main_sell.empty:
                    result['main_sell_amount'] = round(main_sell.sum(), 4)
                if not net_inflow.empty and amount.sum() > 0:
                    result['main_net_inflow'] = round(net_inflow.sum(), 4)
                    result['main_net_inflow_ratio'] = round(net_inflow.sum() / amount.sum(), 4)
            for pe_col, tgt in [('month_end_pe', 'avg_pe'), ('month_end_pe_ttm', 'avg_pe_ttm'), ('month_end_pb', 'avg_pb')]:
                if pe_col in group.columns:
                    values = group[pe_col].replace([np.inf, -np.inf], np.nan).dropna()
                    values = values[(values > 0) & (values < 1000)]
                    if not values.empty:
                        result[tgt] = round(values.mean(), 4)
            pct_cols = {
                'price_tsrank_1y': 'avg_price_tsrank_1y', 'pe_tsrank_1y': 'avg_pe_tsrank_1y',
                'pe_ttm_tsrank_1y': 'avg_pe_ttm_tsrank_1y', 'pb_tsrank_1y': 'avg_pb_tsrank_1y',
            }
            for src, tgt in pct_cols.items():
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
            if all(c in group.columns for c in ['month_end_close', 'ma20', 'ma60', 'ma250']):
                valid = group[['month_end_close', 'ma20', 'ma60', 'ma250']].dropna()
                if not valid.empty:
                    above_ma20 = (valid['month_end_close'] > valid['ma20']).mean()
                    above_ma60 = (valid['month_end_close'] > valid['ma60']).mean()
                    above_ma250 = (valid['month_end_close'] > valid['ma250']).mean()
                    result.update({
                        'above_ma20_ratio': round(above_ma20, 4), 'below_ma20_ratio': round(1 - above_ma20, 4),
                        'above_ma60_ratio': round(above_ma60, 4), 'below_ma60_ratio': round(1 - above_ma60, 4),
                        'above_ma250_ratio': round(above_ma250, 4), 'below_ma250_ratio': round(1 - above_ma250, 4),
                    })
            return result
        except Exception as e:
            self.logger.warning(f"计算月度分组热度失败: {e}")
            return None
