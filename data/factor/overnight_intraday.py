"""隔夜 vs 日内收益分解因子 (Overnight / Intraday)。

物理意义：隔夜收益反映机构定价（开盘前集合竞价），日内收益反映散户博弈。
据此判断资金结构：机构在买+散户在卖 = 最 bullish 信号。

四象限打分：
  Q1: on_mom>0 AND id_mom>0  → score = +1  (机构+散户同向买)
  Q2: on_mom>0 AND id_mom<0  → score = +2  (机构在买，散户在卖，最bullish)
  Q3: on_mom<0 AND id_mom>0  → score = -2  (机构在卖，散户在买，最bearish)
  Q4: on_mom<0 AND id_mom<0  → score = -1  (机构+散户同向卖)

表名：factor_overnight_intraday
主键：index_code + trade_date
数据源：index_daily + sw_daily（UNION ALL，需要 open、close、pre_close）
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

WINDOW = 20
LOOKBACK = WINDOW + 10  # 30 天


class OvernightIntradayCalculator(FactorCalculator):
    """隔夜 vs 日内收益分解因子。"""

    table_name = "overnight_intraday"  # -> factor_overnight_intraday
    primary_keys = ["index_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    output_schema = {
        "index_code": "string",
        "trade_date": "string",
        "overnight_ret": "float",
        "intraday_ret": "float",
        "on_mom": "float",
        "id_mom": "float",
        "score": "int",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("OvernightIntradayCalculator 初始化完成")

    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 index_daily + sw_daily 的 open/close/pre_close。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, LOOKBACK)
        if end_date:
            end_date = end_date.replace("-", "")

        # index_daily 有 open/close/pre_close
        sql_idx = f"""
            SELECT ts_code AS index_code, trade_date, open, close, pre_close
            FROM index_daily
            WHERE 1=1
        """
        if extended_start:
            sql_idx += f" AND trade_date >= '{extended_start}'"
        if end_date:
            sql_idx += f" AND trade_date <= '{end_date}'"

        # sw_daily 有 open/close，但没有 pre_close（用 LAG 推导）
        sql_sw = f"""
            SELECT ts_code AS index_code, trade_date, open, close,
                   LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS pre_close
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
        """计算隔夜/日内因子。"""
        if data.empty:
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["index_code", "trade_date"])

        # 日度分解
        df["overnight_ret"] = np.log(df["open"] / df["pre_close"])
        df["intraday_ret"] = np.log(df["close"] / df["open"])

        # 处理缺失值
        df["overnight_ret"] = df["overnight_ret"].replace([np.inf, -np.inf], None)
        df["intraday_ret"] = df["intraday_ret"].replace([np.inf, -np.inf], None)

        results = []
        for index_code, group in df.groupby("index_code"):
            group = group.set_index("trade_date").sort_index()

            on_ret = group["overnight_ret"]
            id_ret = group["intraday_ret"]

            # 滚动累计
            on_mom_series = on_ret.rolling(window=WINDOW, min_periods=WINDOW).sum()
            id_mom_series = id_ret.rolling(window=WINDOW, min_periods=WINDOW).sum()

            for i in range(LOOKBACK - 1, len(group)):
                current_date = group.index[i]
                row = {
                    "index_code": index_code,
                    "trade_date": current_date,
                    "overnight_ret": on_ret.iloc[i],
                    "intraday_ret": id_ret.iloc[i],
                    "on_mom": on_mom_series.iloc[i],
                    "id_mom": id_mom_series.iloc[i],
                }

                on_m = row["on_mom"]
                id_m = row["id_mom"]
                if pd.notna(on_m) and pd.notna(id_m):
                    if on_m > 0 and id_m > 0:
                        row["score"] = 1   # Q1: 同向买
                    elif on_m > 0 and id_m < 0:
                        row["score"] = 2   # Q2: 机构买，散户卖
                    elif on_m < 0 and id_m > 0:
                        row["score"] = -2  # Q3: 机构卖，散户买
                    elif on_m < 0 and id_m < 0:
                        row["score"] = -1  # Q4: 同向卖
                    else:
                        row["score"] = 0   # 有零值
                else:
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