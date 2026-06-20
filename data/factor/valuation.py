"""估值因子（从 data/factor/valuation.py 迁移到新 BaseCalculator）。

表名：factor_valuation（基类自动加 factor_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖：panel_stock_daily（个股×日 行情宽表，含估值字段）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_previous_n_trading_date
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class ValuationCalculator(FactorCalculator):
    """估值因子计算器。

    估值类因子（PE/PB/PS/股息率/FCFF/FCFE 等）及衍生指标。
    """

    # ===== FactorCalculator 类属性 =====
    table_name = "valuation"  # → factor_valuation
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # 回看窗口
    lookback_period: int = 40

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("ValuationCalculator 初始化完成")

    # ===== output_schema =====
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "pe_ttm": "float", "pe_ttm_abs": "float", "pe_ttm_deret": "float", "pe_ttm_ne": "float",
        "pb": "float", "pb_abs": "float", "pb_deret": "float", "pb_ne": "float",
        "ps_ttm": "float", "ps_ttm_abs": "float", "ps_ttm_deret": "float", "ps_ttm_ne": "float",
        "dv_ttm": "float", "dv_ttm_abs": "float", "dv_ttm_deret": "float", "dv_ttm_ne": "float",
        "total_mv": "float", "total_mv_abs": "float", "total_mv_deret": "float", "total_mv_ne": "float",
        "circ_mv": "float", "circ_mv_abs": "float", "circ_mv_deret": "float", "circ_mv_ne": "float",
        "fcff_ttm": "float", "fcff_ttm_abs": "float", "fcff_ttm_deret": "float", "fcff_ttm_ne": "float",
        "fcfe_ttm": "float", "fcfe_ttm_abs": "float", "fcfe_ttm_deret": "float", "fcfe_ttm_ne": "float",
        "pe_20d_mean": "float", "pe_20d_std": "float", "pe_20d_msr": "float",
        "pe_20d_dif": "float", "pe_20d_dif_ne": "float",
        "pb_20d_mean": "float", "pb_20d_std": "float", "pb_20d_msr": "float",
        "pb_20d_dif": "float", "pb_20d_dif_ne": "float",
        "ps_20d_mean": "float", "ps_20d_std": "float", "ps_20d_msr": "float",
        "ps_20d_dif": "float", "ps_20d_dif_ne": "float",
        "dv_20d_mean": "float", "dv_20d_std": "float", "dv_20d_msr": "float",
        "dv_20d_dif": "float", "dv_20d_dif_ne": "float",
        "fcff_20d_mean": "float", "fcff_20d_std": "float", "fcff_20d_msr": "float",
        "fcff_20d_dif": "float", "fcff_20d_dif_ne": "float",
        "fcfe_20d_mean": "float", "fcfe_20d_std": "float", "fcfe_20d_msr": "float",
        "fcfe_20d_dif": "float", "fcfe_20d_dif_ne": "float",
    }

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
            pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
            total_mv, circ_mv,
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
        """计算估值因子。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        self.logger.info(f"原始数据共 {len(df)} 行")

        end_date = params.get("end_date", "").replace("-", "")
        df = df.sort_values(["ts_code", "trade_date"], ascending=[True, False])
        df = df.groupby("ts_code").head(20).reset_index(drop=True)
        df["trade_date_max"] = df.groupby("ts_code")["trade_date"].transform("max")
        df["trade_days_cnt"] = df.groupby("ts_code")["trade_date"].transform("count")
        df = df[df.trade_date_max.astype(str).str.replace("-", "") == end_date].reset_index(drop=True)
        df = df[df.trade_days_cnt == 20].reset_index(drop=True)
        self.logger.info(f"删除数据后共 {len(df)} 行")

        # 1) 基础指标
        self.logger.info("计算基础指标...")
        df["pe_ttm_abs"] = df["pe_ttm"].abs()
        df["pb_abs"] = df["pb"].abs()
        df["ps_ttm_abs"] = df["ps_ttm"].abs()
        df["dv_ttm_abs"] = df["dv_ttm"].abs()
        df["total_mv_abs"] = df["total_mv"].abs()
        df["circ_mv_abs"] = df["circ_mv"].abs()

        # FCFF/FCFE 用 TTM 现金流近似（这里简化用财务数据派生，实际可从 panel_financial 取）
        # 这里先用占位字段，后续可扩展
        df["fcff_ttm"] = df["total_mv"] / (df["pe_ttm"].abs() + 1e-8)
        df["fcfe_ttm"] = df["circ_mv"] / (df["pe_ttm"].abs() + 1e-8)
        df["fcff_ttm_abs"] = df["fcff_ttm"].abs()
        df["fcfe_ttm_abs"] = df["fcfe_ttm"].abs()

        # 2) 聚合指标
        self.logger.info("计算聚合指标...")
        group = df.groupby("ts_code")

        result = pd.DataFrame()
        result["trade_date"] = group["trade_date"].max()
        result["trade_date"] = pd.to_datetime(result["trade_date"])

        # 当日截面值
        result["pe_ttm"] = group["pe_ttm"].nth(0)
        result["pe_ttm_abs"] = group["pe_ttm_abs"].nth(0)
        result["pb"] = group["pb"].nth(0)
        result["pb_abs"] = group["pb_abs"].nth(0)
        result["ps_ttm"] = group["ps_ttm"].nth(0)
        result["ps_ttm_abs"] = group["ps_ttm_abs"].nth(0)
        result["dv_ttm"] = group["dv_ttm"].nth(0)
        result["dv_ttm_abs"] = group["dv_ttm_abs"].nth(0)
        result["total_mv"] = group["total_mv"].nth(0)
        result["total_mv_abs"] = group["total_mv_abs"].nth(0)
        result["circ_mv"] = group["circ_mv"].nth(0)
        result["circ_mv_abs"] = group["circ_mv_abs"].nth(0)
        result["fcff_ttm"] = group["fcff_ttm"].nth(0)
        result["fcff_ttm_abs"] = group["fcff_ttm_abs"].nth(0)
        result["fcfe_ttm"] = group["fcfe_ttm"].nth(0)
        result["fcfe_ttm_abs"] = group["fcfe_ttm_abs"].nth(0)

        # 20 日均值/标准差/信息比率
        for col, prefix in [
            ("pe_ttm", "pe"), ("pb", "pb"), ("ps_ttm", "ps"),
            ("dv_ttm", "dv"), ("fcff_ttm", "fcff"), ("fcfe_ttm", "fcfe"),
        ]:
            result[f"{prefix}_20d_mean"] = group[col].mean()
            result[f"{prefix}_20d_std"] = group[col].std()
            result[f"{prefix}_20d_msr"] = result[f"{prefix}_20d_mean"].div(result[f"{prefix}_20d_std"])
            result[f"{prefix}_20d_dif"] = result[col] - result[f"{prefix}_20d_mean"]

        result = result.reset_index()
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")
        return result
