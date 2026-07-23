"""动量期限结构因子 (Term Structure)。

物理意义：短期 vs 长期动量的加速/减速状态。正 = 加速（近期强于远期），
负 = 减速。tsmom_ratio 回答了"短期动量是长期的几倍"，截面可比。

表名：factor_momentum_term
主键：index_code + trade_date
数据源：index_daily + sw_daily（UNION ALL）
依赖：index_daily, sw_daily
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)

WINDOW_SHORT = 20
WINDOW_LONG = 60
LOOKBACK = WINDOW_LONG + 10  # 70 天


class MomentumTermCalculator(FactorCalculator):
    """动量期限结构因子。

    ret_20d = ln(P_t / P_{t-20})
    ret_60d = ln(P_t / P_{t-60})
    tsmom_diff = ret_20d - ret_60d（绝对加速度）
    tsmom_ratio = ret_20d / ret_60d（相对加速度）
    score = tsmom_ratio × trend_ok（全部正收益才给分）
    """

    table_name = "momentum_term"  # -> factor_momentum_term
    primary_keys = ["index_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    output_schema = {
        "index_code": "string",
        "trade_date": "string",
        "ret_20d": "float",
        "ret_60d": "float",
        "tsmom_diff": "float",
        "tsmom_ratio": "float",
        "trend_ok": "int",
        "score": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MomentumTermCalculator 初始化完成")

    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 index_daily + sw_daily close 价。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, LOOKBACK)
        if end_date:
            end_date = end_date.replace("-", "")

        sql_idx = f"""
            SELECT ts_code AS index_code, trade_date, close
            FROM index_daily
            WHERE 1=1
        """
        if extended_start:
            sql_idx += f" AND trade_date >= '{extended_start}'"
        if end_date:
            sql_idx += f" AND trade_date <= '{end_date}'"

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
        self.logger.info(f"[1/3] 取价: {extended_start or '开始'}~{end_date or '结束'}")
        df = pd.read_sql(sql, self.engine)
        if df.empty:
            self.logger.warning("无数据")
            return pd.DataFrame()
        self.logger.info(f"取到 {len(df)} 行, {df['index_code'].nunique()} 个指数")
        return df

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """计算期限结构因子。"""
        if data.empty:
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["index_code", "trade_date"])

        results = []
        for index_code, group in df.groupby("index_code"):
            group = group.set_index("trade_date").sort_index()
            close = group["close"]

            for i in range(LOOKBACK - 1, len(close)):
                current_date = close.index[i]
                row = {"index_code": index_code, "trade_date": current_date}

                # 20d ret
                if i >= WINDOW_SHORT:
                    row["ret_20d"] = np.log(close.iloc[i] / close.iloc[i - WINDOW_SHORT])
                else:
                    row["ret_20d"] = None

                # 60d ret
                if i >= WINDOW_LONG:
                    row["ret_60d"] = np.log(close.iloc[i] / close.iloc[i - WINDOW_LONG])
                else:
                    row["ret_60d"] = None

                # 派生指标
                ret_20 = row["ret_20d"]
                ret_60 = row["ret_60d"]
                if ret_20 is not None and ret_60 is not None:
                    row["tsmom_diff"] = ret_20 - ret_60
                    if ret_60 != 0:
                        row["tsmom_ratio"] = ret_20 / ret_60
                    else:
                        row["tsmom_ratio"] = None
                    row["trend_ok"] = 1 if (ret_20 > 0 and ret_60 > 0) else 0
                    row["score"] = row["tsmom_ratio"] * row["trend_ok"] if row["tsmom_ratio"] is not None else 0.0
                else:
                    row["tsmom_diff"] = None
                    row["tsmom_ratio"] = None
                    row["trend_ok"] = None
                    row["score"] = None

                results.append(row)

        if not results:
            self.logger.warning("无有效计算结果")
            return pd.DataFrame()

        result = pd.DataFrame(results)
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")

        self.logger.info(
            f"[2/3] 计算完成: {len(result)} 行, "
            f"score 覆盖={result['score'].notna().sum()}"
        )
        return result