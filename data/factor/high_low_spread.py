"""高低价差因子（从 data/factor/high_low_spread.py 迁移到新 BaseCalculator）。

表名：factor_high_low_spread（基类自动加 factor_ 前缀）
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

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class HighLowSpreadCalculator(FactorCalculator):
    """高低价差因子计算器。

    按个股涨跌排序取换手率/振幅/真实波幅/影线差，聚合 top/bottom 等权/线性衰减/指数衰减。
    """

    # ===== FactorCalculator 类属性 =====
    table_name = "high_low_spread"  # 基类自动加 factor_ 前缀 → factor_high_low_spread
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # 回看窗口（取 40 天保证有 20 天开盘）
    lookback_period: int = 40

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("HighLowSpreadCalculator 初始化完成")

    # ===== output_schema（动态生成，因列多且有规律） =====
    @property
    def output_schema(self) -> dict:  # type: ignore[override]
        """输出 schema：ts_code + trade_date + 4 类指标 × 3 聚合方式 × 多窗口。"""
        schema = {"ts_code": "string", "trade_date": "string"}
        for suffix in ["tvr", "amp", "tr", "plus"]:
            for i in range(20):
                schema[f"close_{i}_{suffix}"] = "float"
            for n in [5, 10]:
                schema[f"close_top{n}_{suffix}"] = "float"
                schema[f"close_top{n}_{suffix}_ld"] = "float"
                schema[f"close_top{n}_{suffix}_exp"] = "float"
                schema[f"close_bottom{n}_{suffix}"] = "float"
                schema[f"close_bottom{n}_{suffix}_ld"] = "float"
                schema[f"close_bottom{n}_{suffix}_exp"] = "float"
            schema[f"{suffix}_mean_20"] = "float"
            schema[f"{suffix}_std_20"] = "float"
        return schema

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 panel_stock_daily 数据（按 trade_date 区间，回看 lookback_period 天）。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, self.lookback_period)
        if end_date:
            end_date = end_date.replace("-", "")

        query = """
        SELECT
            ts_code, trade_date, open, high, low, close, pre_close, adj_factor,
            `change`, pct_chg, pct_chg/100 AS ret, log_return, vol, amount, vwap,
            turnover_rate, turnover_rate_f, total_mv, circ_mv,
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
        """计算高低价差因子。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df = df[df.turnover_rate_f > 0]
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        df["tvr"] = df["turnover_rate_f"]
        df["amp"] = (df["high"] - df["low"]) / df["pre_close"]
        df["tr"] = pd.concat(
            [
                df["high"] - df["low"],
                abs(df["high"] - df["pre_close"]),
                abs(df["low"] - df["pre_close"]),
            ],
            axis=1,
        ).max(axis=1) / df["pre_close"]
        df["plus"] = (2 * df["close"] - df["high"] - df["low"]) / df["pre_close"]

        # 和其他切割变量统一成过去 40 个交易日要求有 20 天开盘
        self.logger.info(f"原始数据共 {len(df)} 行")
        df["open_days"] = df.groupby("ts_code")["ret"].transform("count")
        df = df[df["open_days"] >= 20]
        df = df.sort_values(by=["ts_code", "trade_date"], ascending=[True, False]).reset_index(drop=True)
        df = df.groupby("ts_code").head(20)
        self.logger.info(f"剔除后数据共 {len(df)} 行")

        result = pd.DataFrame()
        result["trade_date"] = df.groupby("ts_code")["trade_date"].max()

        df = df.sort_values(by=["ts_code", "close"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")

        for suffix in ["tvr", "amp", "tr", "plus"]:
            for i in range(20):
                result[f"close_{i}_{suffix}"] = group[f"{suffix}"].nth(i)

        # top5/top10 等权、线性衰减、指数衰减三种聚合方式
        for suffix in ["tvr", "amp", "tr", "plus"]:
            for n in [5, 10]:
                linear_weights = list(np.arange(1, 0, -1 / n))
                exp_weights = [np.power(2, -i / (n - 1)) for i in np.arange(n)]

                result[f"close_top{n}_{suffix}"] = result[
                    [f"close_{i}_{suffix}" for i in range(n)]
                ].sum(axis=1)
                result[f"close_top{n}_{suffix}_ld"] = result[
                    [f"close_{i}_{suffix}" for i in range(n)]
                ].dot(linear_weights)
                result[f"close_top{n}_{suffix}_exp"] = result[
                    [f"close_{i}_{suffix}" for i in range(n)]
                ].dot(exp_weights)

                result[f"close_bottom{n}_{suffix}"] = result[
                    [f"close_{20 - n + i}_{suffix}" for i in range(n)]
                ].sum(axis=1)
                result[f"close_bottom{n}_{suffix}_ld"] = result[
                    [f"close_{20 - n + i}_{suffix}" for i in range(n)]
                ].dot(linear_weights[::-1])
                result[f"close_bottom{n}_{suffix}_exp"] = result[
                    [f"close_{20 - n + i}_{suffix}" for i in range(n)]
                ].dot(exp_weights[::-1])

            result[f"{suffix}_mean_20"] = result[
                [f"close_{i}_{suffix}" for i in range(20)]
            ].mean(axis=1)
            result[f"{suffix}_std_20"] = result[
                [f"close_{i}_{suffix}" for i in range(20)]
            ].std(axis=1)

        # 过滤掉当天不开盘的股票
        end_date = params.get("end_date")
        if end_date:
            end_date = end_date.replace("-", "")
            result["trade_date_str"] = result["trade_date"].astype(str).str.replace("-", "")
            result = result[result["trade_date_str"] == end_date]
            result = result.drop("trade_date_str", axis=1)
        self.logger.info(f"聚合指标计算完成，输出数据 {len(result)} 条记录")

        result = result.reset_index()
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        # trade_date 转回 yyyymmdd 字符串
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")
        return result
