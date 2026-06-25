"""前瞻收益标签（从 data/label/forward_returns.py 迁移到新 BaseCalculator）。

表名：label_forward_returns（基类自动加 label_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖：panel_stock_daily（个股×日 行情宽表）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_next_n_trading_date, get_previous_n_trading_date
from data.label.base import LabelCalculator

logger = logging.getLogger(__name__)


class ForwardReturnsCalculator(LabelCalculator):
    """前瞻收益标签计算器。

    生成 1/5/10/20 日前瞻收益、对数收益、最大回撤、夏普等标签。
    """

    # ===== LabelCalculator 类属性 =====
    table_name = "forward_returns"  # → label_forward_returns
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # 前瞻窗口
    forward_windows: List[int] = [1, 5, 10, 20]
    # 回看 buffer（用于计算当日 vwap 等参考价）
    lookback_period: int = 5

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("ForwardReturnsCalculator 初始化完成")

    # ===== output_schema =====
    @property
    def output_schema(self) -> dict:  # type: ignore[override]
        """输出 schema。"""
        schema = {"ts_code": "string", "trade_date": "string"}
        for n in self.forward_windows:
            schema[f"ret_{n}d"] = "float"
            schema[f"log_ret_{n}d"] = "float"
            schema[f"vw_ret_{n}d"] = "float"
            schema[f"max_up_{n}d"] = "float"
            schema[f"max_down_{n}d"] = "float"
            schema[f"max_drawdown_{n}d"] = "float"
            schema[f"sharpe_{n}d"] = "float"
            schema[f"vol_{n}d"] = "float"
        return schema

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 panel_stock_daily（按 trade_date 区间，向前回看 lookback_period 天）。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, self.lookback_period)
        if end_date:
            end_date = end_date.replace("-", "")

        query = """
        SELECT
            ts_code, trade_date, open, high, low, close, pre_close,
            pct_chg, log_return, vol, amount, vwap,
            turnover_rate_f, total_mv, circ_mv,
            l1_code, l1_name, l2_code, l2_name
        FROM panel_stock_daily
        WHERE 1=1
        """
        if extended_start:
            query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"

        self.logger.info(
            f"取 panel_stock_daily: {extended_start or '开始'}~{end_date or '结束'}, "
            f"股票数: {len(entity_list) if entity_list else '全部'}"
        )
        return pd.read_sql(query, self.engine)

    # ===== process_data =====
    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """计算前瞻收益标签。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["ts_code", "trade_date"], ascending=[True, True]).reset_index(drop=True)
        self.logger.info(f"原始数据共 {len(df)} 行")

        # 计算前瞻收益
        result = df[["ts_code", "trade_date", "close", "vwap"]].copy()
        result["trade_date_str"] = result["trade_date"].dt.strftime("%Y%m%d")

        for n in self.forward_windows:
            # n 日后收盘价
            result[f"close_{n}d_ahead"] = df.groupby("ts_code")["close"].shift(-n)
            result[f"high_{n}d_ahead"] = df.groupby("ts_code")["high"].shift(-n)
            result[f"low_{n}d_ahead"] = df.groupby("ts_code")["low"].shift(-n)
            result[f"vwap_{n}d_ahead"] = df.groupby("ts_code")["vwap"].shift(-n)

            # n 日内最高/最低
            high_n = (
                df.groupby("ts_code")["high"]
                .rolling(window=n, min_periods=1)
                .max()
                .shift(-n + 1)
                .reset_index(level=0, drop=True)
            )
            low_n = (
                df.groupby("ts_code")["low"]
                .rolling(window=n, min_periods=1)
                .min()
                .shift(-n + 1)
                .reset_index(level=0, drop=True)
            )
            result[f"max_high_{n}d"] = high_n
            result[f"min_low_{n}d"] = low_n

            # 前瞻收益
            result[f"ret_{n}d"] = result[f"close_{n}d_ahead"] / result["close"] - 1
            result[f"log_ret_{n}d"] = np.log(result[f"close_{n}d_ahead"] / result["close"])
            result[f"vw_ret_{n}d"] = result[f"vwap_{n}d_ahead"] / result["vwap"] - 1

            # 最大上涨/下跌
            result[f"max_up_{n}d"] = result[f"max_high_{n}d"] / result["close"] - 1
            result[f"max_down_{n}d"] = result[f"min_low_{n}d"] / result["close"] - 1

            # 最大回撤（n 日内）
            cummax = result[f"close_{n}d_ahead"].fillna(result["close"])
            # 简化：用 close 序列的滚动 cummax
            close_ahead = df.groupby("ts_code")["close"].shift(-n)
            rolling_max = (
                df.groupby("ts_code")["close"]
                .rolling(window=n + 1, min_periods=1)
                .max()
                .shift(-n)
                .reset_index(level=0, drop=True)
            )
            result[f"max_drawdown_{n}d"] = (close_ahead - rolling_max) / rolling_max

            # n 日波动率与夏普
            ret = df.groupby("ts_code")["pct_chg"].shift(-n) / 100
            rolling_std = (
                df.groupby("ts_code")["pct_chg"]
                .rolling(window=n, min_periods=1)
                .std()
                .shift(-n + 1)
                .reset_index(level=0, drop=True)
            ) / 100
            result[f"vol_{n}d"] = rolling_std * np.sqrt(252)
            result[f"sharpe_{n}d"] = result[f"ret_{n}d"] / (result[f"vol_{n}d"] + 1e-8)

            # 清理临时列
            result = result.drop(
                [f"close_{n}d_ahead", f"high_{n}d_ahead", f"low_{n}d_ahead",
                 f"vwap_{n}d_ahead", f"max_high_{n}d", f"min_low_{n}d"],
                axis=1,
            )

        # 过滤到目标 end_date
        end_date = params.get("end_date")
        if end_date:
            end_date = end_date.replace("-", "")
            result = result[result["trade_date_str"] == end_date]

        result = result.drop(["trade_date_str", "close", "vwap"], axis=1, errors="ignore")
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")
        self.logger.info(f"前瞻收益标签计算完成，输出数据 {len(result)} 条记录")
        return result
