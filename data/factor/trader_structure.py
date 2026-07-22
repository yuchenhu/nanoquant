"""交易者结构因子（从 data/factor/trader_structure.py 迁移到新 BaseCalculator）。

表名：factor_trader_structure（基类自动加 factor_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖：panel_stock_daily（个股×日 行情宽表，含资金流字段）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class TraderStructureCalculator(FactorCalculator):
    """交易者结构因子计算器。

    按照交易者结构切割涨跌幅，聚合 top/bottom 等权/线性衰减/指数衰减。
    """

    # ===== FactorCalculator 类属性 =====
    table_name = "trader_structure"  # → factor_trader_structure
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    # 回看窗口
    lookback_period: int = 40

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("TraderStructureCalculator 初始化完成")

    # ===== output_schema =====
    @property
    def output_schema(self) -> dict:  # type: ignore[override]
        """输出 schema。"""
        schema = {"ts_code": "string", "trade_date": "string"}
        for prefix in ["buy_sm", "buy_elg", "sm_imba", "elg_imba"]:
            for i in range(20):
                schema[f"{prefix}_{i}_ret"] = "float"
            for n in [5, 10]:
                for agg in ["", "_ld", "_exp"]:
                    schema[f"{prefix}_top{n}{agg}"] = "float"
                    schema[f"{prefix}_bottom{n}{agg}"] = "float"
        return schema

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 panel_stock_daily（按 trade_date 区间，回看 lookback_period 天）。"""
        extended_start = None
        if start_date:
            start_date = start_date.replace("-", "")
            extended_start = get_previous_n_trading_date(start_date, self.lookback_period)
        if end_date:
            end_date = end_date.replace("-", "")

        query = """
        SELECT
            ts_code, trade_date, pct_chg/100 as ret, vol, turnover_rate_f,
            buy_sm_vol, sell_sm_vol, buy_md_vol, sell_md_vol,
            buy_lg_vol, sell_lg_vol, buy_elg_vol, sell_elg_vol,
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
        """计算交易者结构因子。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df = df[df.turnover_rate_f > 0]
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["vm_ret"] = df["vol"] * df["ret"]

        df["buy_sm_vol_ratio"] = df["buy_sm_vol"] / df["vol"]
        df["buy_elg_vol_ratio"] = df["buy_elg_vol"] / df["vol"]

        df["sm_vol"] = df["buy_sm_vol"] + df["sell_sm_vol"]
        df["net_sm_vol"] = df["buy_sm_vol"] - df["sell_sm_vol"]
        df["sm_imba"] = df["net_sm_vol"] / df["sm_vol"]

        df["elg_vol"] = df["buy_elg_vol"] + df["sell_elg_vol"]
        df["net_elg_vol"] = df["buy_elg_vol"] - df["sell_elg_vol"]
        df["elg_imba"] = df["net_elg_vol"] / df["elg_vol"]

        # 和其他切割变量统一成过去 40 个交易日要求有 20 天开盘且有资金流数据
        self.logger.info(f"原始数据共 {len(df)} 行")
        df["open_days"] = df.groupby("ts_code")["ret"].transform("count")
        df["moneyflow_days"] = df.groupby("ts_code")["buy_sm_vol"].transform("count")
        df = df[(df.open_days >= 20) & (df.moneyflow_days >= 20)]
        df = df.sort_values(by=["ts_code", "trade_date"], ascending=[True, False]).reset_index(drop=True)
        df = df.groupby("ts_code").head(20)
        self.logger.info(f"剔除后数据共 {len(df)} 行")

        result = pd.DataFrame()
        result["trade_date"] = df.groupby("ts_code")["trade_date"].max()

        # 按照交易者结构切割涨跌幅
        df = df.sort_values(by=["ts_code", "buy_sm_vol_ratio"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")
        for i in range(20):
            result[f"buy_sm_{i}_ret"] = group["ret"].nth(i)

        df = df.sort_values(by=["ts_code", "buy_elg_vol_ratio"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")
        for i in range(20):
            result[f"buy_elg_{i}_ret"] = group["ret"].nth(i)

        df = df.sort_values(by=["ts_code", "sm_imba"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")
        for i in range(20):
            result[f"sm_imba_{i}_ret"] = group["ret"].nth(i)

        df = df.sort_values(by=["ts_code", "elg_imba"], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby("ts_code")
        for i in range(20):
            result[f"elg_imba_{i}_ret"] = group["ret"].nth(i)

        # top5/top10 等权、线性衰减、指数衰减三种聚合方式
        for prefix in ["buy_sm", "buy_elg", "sm_imba", "elg_imba"]:
            for n in [5, 10]:
                linear_weights = list(np.arange(1, 0, -1 / n))
                exp_weights = [np.power(2, -i / (n - 1)) for i in np.arange(n)]

                result[f"{prefix}_top{n}"] = result[
                    [f"{prefix}_{i}_ret" for i in range(n)]
                ].sum(axis=1)
                result[f"{prefix}_top{n}_ld"] = result[
                    [f"{prefix}_{i}_ret" for i in range(n)]
                ].dot(linear_weights)
                result[f"{prefix}_top{n}_exp"] = result[
                    [f"{prefix}_{i}_ret" for i in range(n)]
                ].dot(exp_weights)

                result[f"{prefix}_bottom{n}"] = result[
                    [f"{prefix}_{20 - n + i}_ret" for i in range(n)]
                ].sum(axis=1)
                result[f"{prefix}_bottom{n}_ld"] = result[
                    [f"{prefix}_{20 - n + i}_ret" for i in range(n)]
                ].dot(linear_weights[::-1])
                result[f"{prefix}_bottom{n}_exp"] = result[
                    [f"{prefix}_{20 - n + i}_ret" for i in range(n)]
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
