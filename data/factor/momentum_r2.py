"""趋势一致性动量因子 (Momentum x R^2)。

物理意义：区分"稳步攀升"和"陡拉"。R^2 高意味着趋势持续性更强。
纯动量只度量"涨了多少"，不关心"怎么涨的"。一条陡拉涨停的路径和
一条稳步攀升的路径，在纯动量下得分相同，但前者后续反转概率高，
后者趋势持续性高。R^2 区分了这两种路径。

表名：factor_momentum_r2
主键：index_code + trade_date
数据源：index_daily + sw_daily（UNION ALL）
依赖：index_daily, sw_daily
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)

# 两个窗口
WINDOWS = [20, 60]
LOOKBACK = max(WINDOWS) + 10  # 70 天，覆盖停牌/缺失 buffer


class MomentumR2Calculator(FactorCalculator):
    """趋势一致性动量因子。

    对每个指数，用过去 N 日收盘价做 OLS 线性回归，计算 R^2。
    综合得分 = sign(ret) * |ret| * R^2。
    """

    table_name = "momentum_r2"  # -> factor_momentum_r2
    primary_keys = ["index_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    output_schema = {
        "index_code": "string",
        "trade_date": "string",
        "ret_20d": "float",
        "r2_20d": "float",
        "beta_20d": "float",
        "score_20d": "float",
        "ret_60d": "float",
        "r2_60d": "float",
        "beta_60d": "float",
        "score_60d": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MomentumR2Calculator 初始化完成")

    # ================================================================
    # get_data
    # ================================================================

    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 index_daily + sw_daily close 价（回看 LOOKBACK 天）。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, LOOKBACK)
        if end_date:
            end_date = end_date.replace("-", "")

        # index_daily（宽基 + 风格指数）
        sql_idx = f"""
            SELECT ts_code AS index_code, trade_date, close
            FROM index_daily
            WHERE 1=1
        """
        if extended_start:
            sql_idx += f" AND trade_date >= '{extended_start}'"
        if end_date:
            sql_idx += f" AND trade_date <= '{end_date}'"

        # sw_daily（申万行业指数）
        sql_sw = f"""
            SELECT ts_code AS index_code, trade_date, close
            FROM sw_daily
            WHERE 1=1
        """
        if extended_start:
            sql_sw += f" AND trade_date >= '{extended_start}'"
        if end_date:
            sql_sw += f" AND trade_date <= '{end_date}'"

        sql = f"({sql_idx}) UNION ALL ({sql_sw}) ORDER BY index_code, trade_date"

        self.logger.info(
            f"[1/3] 取价: {extended_start or '开始'}~{end_date or '结束'}"
        )
        df = pd.read_sql(sql, self.engine)
        if df.empty:
            self.logger.warning("index_daily + sw_daily 无数据")
            return pd.DataFrame()
        self.logger.info(f"取到 {len(df)} 行, {df['index_code'].nunique()} 个指数")
        return df

    # ================================================================
    # process_data
    # ================================================================

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """计算动量xR^2 因子。"""
        if data.empty:
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["index_code", "trade_date"])

        results = []
        for index_code, group in df.groupby("index_code"):
            group = group.set_index("trade_date").sort_index()
            close = group["close"]

            # 对每个交易日，计算当前窗口的指标
            for i in range(LOOKBACK - 1, len(close)):
                current_date = close.index[i]
                row = {"index_code": index_code, "trade_date": current_date}

                for w in WINDOWS:
                    window_close = close.iloc[i - w + 1 : i + 1]
                    if len(window_close) < w:
                        # 数据不足，全部 NULL
                        row[f"ret_{w}d"] = None
                        row[f"r2_{w}d"] = None
                        row[f"beta_{w}d"] = None
                        row[f"score_{w}d"] = None
                        continue

                    prices = window_close.values
                    log_ret = np.log(prices[-1] / prices[0])
                    r2, beta = _calc_ols_r2(prices)

                    row[f"ret_{w}d"] = log_ret
                    row[f"r2_{w}d"] = r2
                    row[f"beta_{w}d"] = beta
                    row[f"score_{w}d"] = np.sign(log_ret) * abs(log_ret) * r2 if r2 else 0.0

                results.append(row)

        if not results:
            self.logger.warning("无有效计算结果")
            return pd.DataFrame()

        result = pd.DataFrame(results)
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")

        self.logger.info(
            f"[2/3] 计算完成: {len(result)} 行, "
            f"ret_20d 覆盖={result['ret_20d'].notna().sum()}, "
            f"score_60d 覆盖={result['score_60d'].notna().sum()}"
        )
        return result


# ================================================================
# 工具函数
# ================================================================

def _calc_ols_r2(prices: np.ndarray) -> tuple[float, float]:
    """对 prices 做 OLS 线性回归，返回 (R^2, beta)。

    y = prices (N 个收盘价)
    x = 1, 2, ..., N（时间序列）
    R^2 = 1 - SS_res / SS_tot
    beta = 斜率（日均涨跌幅，非年化）
    """
    N = len(prices)
    x = np.arange(1, N + 1, dtype=float)
    x_mean = x.mean()
    y_mean = prices.mean()

    # OLS beta
    beta = np.sum((x - x_mean) * (prices - y_mean)) / np.sum((x - x_mean) ** 2)
    alpha = y_mean - beta * x_mean

    # R^2
    y_pred = alpha + beta * x
    ss_res = np.sum((prices - y_pred) ** 2)
    ss_tot = np.sum((prices - y_mean) ** 2)

    if ss_tot == 0:
        return 0.0, 0.0  # 价格不变，beta=0, R^2 无意义
    r2 = 1.0 - ss_res / ss_tot
    return float(r2), float(beta)