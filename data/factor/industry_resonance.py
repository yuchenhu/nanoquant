"""行业共振因子（从 data/factor/industry_resonance.py 迁移到新 BaseCalculator）。

表名：factor_industry_resonance（基类自动加 factor_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖：panel_stock_daily（个股×日 行情宽表）+ sw_daily（申万行业指数日线）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class IndustryResonanceCalculator(FactorCalculator):
    """行业共振因子计算器。

    按个股涨跌×成交量排序取行业动量，线性衰减后加权求和。
    """

    # ===== FactorCalculator 类属性 =====
    table_name = "industry_resonance"  # → factor_industry_resonance
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # 回看窗口
    lookback_period: int = 40

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("IndustryResonanceCalculator 初始化完成")

    # ===== output_schema =====
    @property
    def output_schema(self) -> dict:  # type: ignore[override]
        """输出 schema。"""
        schema = {"ts_code": "string", "trade_date": "string"}
        for i in range(20):
            schema[f"ind_ret_{i}_ret"] = "float"
            schema[f"ret_{i}_ind_ret"] = "float"
            schema[f"vm_ret_{i}_ind_ret"] = "float"
        for n in [5, 10, 15]:
            for agg in ["", "_ld", "_exp"]:
                schema[f"vm_ret_top{n}_ind_ret{agg}"] = "float"
                schema[f"vm_ret_bottom{n}_ind_ret{agg}"] = "float"
        return schema

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 panel_stock_daily + sw_daily（按 trade_date 区间，回看 lookback_period 天）。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, self.lookback_period)
        if end_date:
            end_date = end_date.replace("-", "")

        stock_query = """
        SELECT
            ts_code, trade_date, pct_chg/100 as ret, vol, turnover_rate_f,
            l1_code, l1_name, l2_code, l2_name
        FROM panel_stock_daily
        WHERE 1=1
        """
        if extended_start:
            stock_query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            stock_query += f" AND trade_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            stock_query += f" AND ts_code IN ({codes_str})"

        self.logger.info(
            f"取 panel_stock_daily: {extended_start or '开始'}~{end_date or '结束'}, "
            f"股票数: {len(entity_list) if entity_list else '全部'}"
        )
        stock_df = pd.read_sql(stock_query, self.engine)

        industry_query = """
        SELECT ts_code, trade_date, pct_change/100 as ind_ret
        FROM sw_daily
        WHERE 1=1
        """
        if extended_start:
            industry_query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            industry_query += f" AND trade_date <= '{end_date}'"

        self.logger.info(f"取 sw_daily: {extended_start or '开始'}~{end_date or '结束'}")
        industry_df = pd.read_sql(industry_query, self.engine)
        industry_df = industry_df.rename(columns={"ts_code": "l1_code"})

        df = pd.merge(
            stock_df, industry_df,
            left_on=["l1_code", "trade_date"],
            right_on=["l1_code", "trade_date"],
            how="left",
        )
        return df

    # ===== process_data =====
    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """计算行业共振因子。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df = df[df.turnover_rate_f > 0]
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["vm_ret"] = df["vol"] * df["ret"]

        # 和其他切割变量统一成过去 40 个交易日要求有 20 天开盘
        self.logger.info(f"原始数据共 {len(df)} 行")
        df["open_days"] = df.groupby("ts_code")["ret"].transform("count")
        df["ind_open_days"] = df.groupby("ts_code")["ind_ret"].transform("count")
        df = df[(df.open_days >= 20) & (df.ind_open_days >= 20)]
        df = df.sort_values(by=["ts_code", "trade_date"], ascending=[True, False]).reset_index(drop=True)
        df = df.groupby("ts_code").head(20)
        self.logger.info(f"剔除后数据共 {len(df)} 行")

        # 按照个股涨跌*成交量排序取行业动量，线性衰减后加权求和
        df = df.sort_values(by=["ts_code", "ind_ret"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")

        result = pd.DataFrame()
        result["trade_date"] = group["trade_date"].max()

        for i in range(20):
            result[f"ind_ret_{i}_ret"] = group["ret"].nth(i)

        df = df.sort_values(by=["ts_code", "ret"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")

        for i in range(20):
            result[f"ret_{i}_ind_ret"] = group["ind_ret"].nth(i)

        df = df.sort_values(by=["ts_code", "vm_ret"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")

        for i in range(20):
            result[f"vm_ret_{i}_ind_ret"] = group["ind_ret"].nth(i)

        for n in [5, 10, 15]:
            linear_weights = list(np.arange(1, 0, -1 / n))
            exp_weights = [np.power(2, -i / (n - 1)) for i in np.arange(n)]

            result[f"vm_ret_top{n}_ind_ret"] = result[
                [f"vm_ret_{i}_ind_ret" for i in range(n)]
            ].sum(axis=1)
            result[f"vm_ret_top{n}_ind_ret_ld"] = result[
                [f"vm_ret_{i}_ind_ret" for i in range(n)]
            ].dot(linear_weights)
            result[f"vm_ret_top{n}_ind_ret_exp"] = result[
                [f"vm_ret_{i}_ind_ret" for i in range(n)]
            ].dot(exp_weights)

            result[f"vm_ret_bottom{n}_ind_ret"] = result[
                [f"vm_ret_{20 - n + i}_ind_ret" for i in range(n)]
            ].sum(axis=1)
            result[f"vm_ret_bottom{n}_ind_ret_ld"] = result[
                [f"vm_ret_{20 - n + i}_ind_ret" for i in range(n)]
            ].dot(linear_weights[::-1])
            result[f"vm_ret_bottom{n}_ind_ret_exp"] = result[
                [f"vm_ret_{20 - n + i}_ind_ret" for i in range(n)]
            ].dot(exp_weights[::-1])

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
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")
        return result
