"""个股×日 技术指标 + 时序分位预计算（MA / 波动率 / above_ma / 1y-5y 分位）。

表名：panel_stock_percentiles
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert
依赖：panel_stock_daily

================================ 性能优化 ================================
1. 窄列取数：SELECT 精确 8 列（close / pe / pe_ttm / pb / turnover_rate / pct_chg / adj_factor）
   替代 panel_stock_daily 的 60+ 列全读，IO 减少 ~85%。
2. rolling.rank() 替代 percentileofscore：原生 C/cython 实现，比 Python lambda 快一个数量级。
   公式：(rank-1)/(cnt-1)，等价于"当前值在窗口中除自己外的前 N-1 个值中的百分位"。
3. 不引入中间表：窄 SELECT 已等效，不增加维护成本。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class StockPercentilesCalculator(PanelCalculator):
    """个股×日 技术指标 + 时序分位（1y/3y/5y + above_ma + MA + 波动率）。"""

    table_name = "stock_percentiles"  # → panel_stock_percentiles
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        # === 透传（方便下游直接 JOIN） ===
        "close": "float", "pe": "float", "pe_ttm": "float", "pb": "float",
        "turnover_rate": "float", "pct_chg": "float",
        # === 均线 ===
        "ma20": "float", "ma60": "float", "ma250": "float",
        # === 均线相对位置（0/1 标记，下游直接 count→ratio） ===
        "above_ma20": "float",
        "above_ma60": "float",
        "above_ma250": "float",
        # === 波动率 ===
        "volatility_20": "float", "volatility_60": "float", "volatility_250": "float",
        # === 1y 时序分位 ===
        "price_tsrank_1y": "float", "pe_tsrank_1y": "float",
        "pe_ttm_tsrank_1y": "float", "pb_tsrank_1y": "float",
        # === 3y 时序分位 ===
        "price_tsrank_3y": "float", "pe_tsrank_3y": "float",
        "pe_ttm_tsrank_3y": "float", "pb_tsrank_3y": "float",
        # === 5y 时序分位 ===
        "price_tsrank_5y": "float", "pe_tsrank_5y": "float",
        "pe_ttm_tsrank_5y": "float", "pb_tsrank_5y": "float",
    }

    # 窗口参数
    WINDOWS = {
        "1y": 250,
        "3y": 750,
        "5y": 1250,
    }
    MIN_HISTORY_DAYS = 120
    MAX_WINDOW = max(WINDOWS.values())  # 1250

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("StockPercentilesCalculator 初始化")

    # ===== get_data：窄列读取 =====

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """窄列读取 panel_stock_daily（只取 8 列，不读 60+ 列的宽表全线）。

        往前扩展 MAX_WINDOW + 200 = 1450 交易日，保证 5y 滚动窗口首日有足够历史。
        """
        extended_start = None
        if start_date:
            sd = start_date.replace('-', '')
            extended_start = get_previous_n_trading_date(sd, self.MAX_WINDOW + 200)
        query = """
        SELECT ts_code, trade_date, close, pe, pe_ttm, pb,
               turnover_rate, pct_chg, adj_factor
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
            f"取 panel_stock_daily(窄列): {extended_start or '开始'}~{end_date or '结束'}, "
            f"股票数: {len(entity_list) if entity_list else '全部'}"
        )
        return pd.read_sql(query, self.engine)

    # ===== process_data =====

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

    # ===== 单只股票处理 =====

    def _process_single_stock(self, stock_data: pd.DataFrame) -> pd.DataFrame:
        stock_data = stock_data.sort_values('trade_date').copy()
        stock_data['adj_close'] = stock_data['close'] * stock_data['adj_factor']
        stock_data = self._calculate_technical_indicators(stock_data)
        stock_data = self._calculate_rolling_percentiles(stock_data)
        cols = [
            'ts_code', 'trade_date', 'close', 'pe', 'pe_ttm', 'pb',
            'turnover_rate', 'pct_chg',
            'ma20', 'ma60', 'ma250',
            'above_ma20', 'above_ma60', 'above_ma250',
            'volatility_20', 'volatility_60', 'volatility_250',
            'price_tsrank_1y', 'pe_tsrank_1y', 'pe_ttm_tsrank_1y', 'pb_tsrank_1y',
            'price_tsrank_3y', 'pe_tsrank_3y', 'pe_ttm_tsrank_3y', 'pb_tsrank_3y',
            'price_tsrank_5y', 'pe_tsrank_5y', 'pe_ttm_tsrank_5y', 'pb_tsrank_5y',
        ]
        available = [c for c in cols if c in stock_data.columns]
        return stock_data[available]

    # ===== 技术指标 =====

    def _calculate_technical_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """MA + 波动率 + above_ma 标记。"""
        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()
        data['ma250'] = data['close'].rolling(250).mean()
        data['above_ma20'] = (data['close'] > data['ma20']).astype(float)
        data['above_ma60'] = (data['close'] > data['ma60']).astype(float)
        data['above_ma250'] = (data['close'] > data['ma250']).astype(float)
        data['volatility_20'] = data['pct_chg'].rolling(20).std()
        data['volatility_60'] = data['pct_chg'].rolling(60).std()
        data['volatility_250'] = data['pct_chg'].rolling(250).std()
        return data

    # ===== 时序分位（rolling.rank） =====

    def _calculate_rolling_percentiles(self, data: pd.DataFrame) -> pd.DataFrame:
        """所有窗口 × 所有指标的时序分位，用原生 rolling.rank() 替代 percentileofscore。

        公式：(rank - 1) / (cnt - 1)
        - rank: 当前值在窗口中的排位（1-based，average 处理 ties）
        - cnt: 窗口内非 NaN 值的个数
        - 等价于：当前值在除自己外的前 N-1 个历史值中的百分位
        """
        metrics = [
            ('price', 'adj_close'),
            ('pe', 'pe'),
            ('pe_ttm', 'pe_ttm'),
            ('pb', 'pb'),
        ]
        for name, src in metrics:
            if src not in data.columns:
                continue
            series = data[src]
            for label, window in self.WINDOWS.items():
                col = f'{name}_tsrank_{label}'
                rank = series.rolling(window, min_periods=self.MIN_HISTORY_DAYS).rank()
                cnt = series.rolling(window, min_periods=self.MIN_HISTORY_DAYS).count()
                data[col] = (rank - 1) / (cnt - 1)
                # 窗口内值太少时结果不可靠，但不额外置 NaN（min_periods 已控制）
        return data
