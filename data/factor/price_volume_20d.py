"""20 日量价因子（从 data/factor/price_volume_20d.py 迁移到新 BaseCalculator）。

表名：factor_price_volume_20d（基类自动加 factor_ 前缀）
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
from core.preprocessing import (
    mad_winsorize,
    neutralize_factor,
    orthogonalize_factor,
    rank_factor,
)
from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class PriceVolume20DCalculator(FactorCalculator):
    """20 日经典量价因子计算器。

    从计算开销考虑用相同取数字段、计算周期的指标放在一起。
    """

    # ===== FactorCalculator 类属性 =====
    table_name = "price_volume_20d"  # → factor_price_volume_20d
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    # 最大回看时间，取 20*2=40 天，留下个股停牌造成交易日期少于交易日历的 buffer
    window: int = 20
    lookback_period: int = 40

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("PriceVolume20DCalculator 初始化完成")

    # ===== output_schema =====
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "ret_20": "float", "ret_mean_20": "float", "ret_std_20": "float", "ret_msr_20": "float",
        "vw_ret_20": "float", "intraday_ret_20": "float", "vw_intraday_ret_20": "float",
        "overnight_ret_20": "float", "abs_overnight_ret_20": "float",
        "tvr_mean_20": "float", "tvr_std_20": "float", "tvr_msr_20": "float", "utr": "float",
        "tvr_chg_mean_20": "float", "tvr_chg_std_20": "float", "tvr_chg_msr_20": "float",
        "amp_mean_20": "float", "amp_std_20": "float", "amp_msr_20": "float",
        "shadow_diff_mean_20": "float", "shadow_diff_std_20": "float", "shadow_diff_msr_20": "float",
        "plus_mean_20": "float", "plus_std_20": "float", "plus_msr_20": "float",
        "illiq_mean_20": "float", "illiq_std_20": "float", "illiq_msr_20": "float",
        "tvr_deplus_mean_20": "float", "tvr_deplus_std_20": "float",
        "plus_detvr_mean_20": "float", "plus_detvr_std_20": "float",
        "tvr_ne_deplus_mean_20": "float", "tvr_ne_deplus_std_20": "float",
        "plus_ne_detvr_mean_20": "float", "plus_ne_detvr_std_20": "float",
        "sm_imba_norm": "float", "sm_imba_deret": "float", "ret_de_sm_imba": "float",
        "elg_imba_norm": "float", "elg_imba_deret": "float", "ret_de_elg_imba": "float",
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
            ts_code, trade_date, open, high, low, close, pre_close, adj_factor,
            `change`, pct_chg, log_return, vol, amount, vwap,
            buy_sm_vol, sell_sm_vol, buy_md_vol, sell_md_vol,
            buy_lg_vol, sell_lg_vol, buy_elg_vol, sell_elg_vol,
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
        """计算 20 日量价因子。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        self.logger.info(f"原始数据共 {len(df)} 行")

        end_date = params.get("end_date", "").replace("-", "")
        df = df.sort_values(["ts_code", "trade_date"], ascending=[True, False])
        df = df.groupby("ts_code").head(20).reset_index(drop=True)
        df["trade_date_max"] = df.groupby("ts_code")["trade_date"].transform("max")
        df["trade_days_cnt"] = df.groupby("ts_code")["trade_date"].transform("count")
        df = df[df.trade_date_max.astype(str).str.replace("-", "") == end_date].reset_index(drop=True)
        df = df[df.trade_days_cnt == self.window].reset_index(drop=True)
        self.logger.info(f"删除数据后共 {len(df)} 行")

        # 1）基础指标
        self.logger.info("计算基础指标...")
        df["ret"] = df["pct_chg"] / 100
        df["intraday_ret"] = df["close"] / df["open"] - 1
        df["overnight_ret"] = df["open"] / df["pre_close"] - 1
        df["abs_overnight_ret"] = df["overnight_ret"].abs()
        df["vm_ret"] = df["ret"] * df["vol"]
        df["vm_intraday_ret"] = df["intraday_ret"] * df["vol"]

        df["amplitude"] = (df["high"] - df["low"]) / df["pre_close"]
        df["true_range"] = pd.concat(
            [
                df["high"] - df["low"],
                abs(df["high"] - df["pre_close"]),
                abs(df["low"] - df["pre_close"]),
            ],
            axis=1,
        ).max(axis=1) / df["pre_close"]
        df["body"] = abs(df["close"] - df["open"]) / df["pre_close"]

        # 传统的上下影线、影线差定义，以及高子剑研报影线差定义，下减上是正向
        df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["pre_close"]
        df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["pre_close"]
        df["shadow_diff"] = df["lower_shadow"] - df["upper_shadow"]
        df["plus"] = (2 * df["close"] - df["high"] - df["low"]) / df["pre_close"]

        df["pre_ret"] = df.groupby("ts_code")["ret"].shift(1)
        df["pre_vol"] = df.groupby("ts_code")["vol"].shift(1)
        df["pre_tvr"] = df.groupby("ts_code")["turnover_rate_f"].shift(1)
        df["tvr_chg"] = df["turnover_rate_f"] / df["pre_tvr"] - 1
        df["vol_chg"] = df["vol"] / df["pre_vol"] - 1

        df["sm_vol"] = df["buy_sm_vol"] + df["sell_sm_vol"]
        df["net_sm_vol"] = df["buy_sm_vol"] - df["sell_sm_vol"]
        df["abs_net_sm_vol"] = df["net_sm_vol"].abs()
        df["sm_vol_imba"] = df["net_sm_vol"] / df["sm_vol"]
        df["buy_sm_vol_ratio"] = df["buy_sm_vol"] / df["vol"]
        df["sell_sm_vol_ratio"] = df["sell_sm_vol"] / df["vol"]
        df["sm_vol_ratio"] = df["sm_vol"] / (df["vol"] * 2)

        df["elg_vol"] = df["buy_elg_vol"] + df["sell_elg_vol"]
        df["net_elg_vol"] = df["buy_elg_vol"] - df["sell_elg_vol"]
        df["abs_net_elg_vol"] = df["net_elg_vol"].abs()
        df["elg_vol_imba"] = df["net_elg_vol"] / df["elg_vol"]
        df["buy_elg_vol_ratio"] = df["buy_elg_vol"] / df["vol"]
        df["sell_elg_vol_ratio"] = df["sell_elg_vol"] / df["vol"]
        df["elg_vol_ratio"] = df["elg_vol"] / (df["vol"] * 2)

        df["illiq"] = np.abs(df["ret"]) / (df["amount"] / 10000)

        # 高子剑研报，用 plus 和换手率互相提纯
        self.logger.info("计算残差指标...")
        df["tvr_clean"] = mad_winsorize(df, "turnover_rate_f", date_col="trade_date")
        df["plus_clean"] = mad_winsorize(df, "plus", date_col="trade_date")
        df["tvr_deplus"] = orthogonalize_factor(
            df, target_factor="tvr_clean", control_factors=["plus_clean"], date_col="trade_date"
        )
        df["plus_detvr"] = orthogonalize_factor(
            df, target_factor="plus_clean", control_factors=["tvr_clean"], date_col="trade_date"
        )
        # 先行业市值中性化，再互相取残差
        df["tvr_ne"] = neutralize_factor(df, "turnover_rate_f", date_col="trade_date")
        df["plus_ne"] = neutralize_factor(df, "plus", date_col="trade_date")
        df["tvr_ne_deplus"] = orthogonalize_factor(
            df, target_factor="tvr_ne", control_factors=["plus_ne"], date_col="trade_date"
        )
        df["plus_ne_detvr"] = orthogonalize_factor(
            df, target_factor="plus_ne", control_factors=["tvr_ne"], date_col="trade_date"
        )
        self.logger.info("基础指标计算完成")

        # 2) 聚合指标
        self.logger.info("计算聚合指标...")
        group = df.groupby("ts_code")

        result = pd.DataFrame()
        result["trade_date"] = group["trade_date"].max()
        result["trade_date"] = pd.to_datetime(result["trade_date"])

        result["ret_20"] = np.exp(group["log_return"].sum()) - 1
        result["ret_mean_20"] = group["ret"].mean()
        result["ret_std_20"] = group["ret"].std()
        result["ret_msr_20"] = result["ret_mean_20"].div(result["ret_std_20"])

        result["vw_ret_20"] = group["vm_ret"].sum().div(group["vol"].sum())
        result["intraday_ret_20"] = group["intraday_ret"].mean()
        result["vw_intraday_ret_20"] = group["vm_intraday_ret"].sum().div(group["vol"].sum())
        result["overnight_ret_20"] = group["overnight_ret"].mean()
        result["abs_overnight_ret_20"] = group["abs_overnight_ret"].mean()

        # 换手率
        result["tvr_mean_20"] = group["turnover_rate_f"].mean()
        result["tvr_std_20"] = group["turnover_rate_f"].std()
        result["tvr_msr_20"] = result["tvr_mean_20"].div(result["tvr_std_20"])

        # 高子剑 U 形换手率
        result["tvr_std_20_rk"] = rank_factor(result, "tvr_std_20", date_col="trade_date")
        small = (1 - rank_factor(result[result.tvr_std_20_rk < 0.5], "tvr_mean_20", date_col="trade_date")) * 0.5
        large = rank_factor(result[result.tvr_std_20_rk >= 0.5], "tvr_mean_20", date_col="trade_date") * 0.5
        total = pd.concat([small, large], axis=0)
        total = pd.DataFrame(total, columns=["tvr_mean_20_mod"])
        result = pd.merge(result, total, left_index=True, right_index=True, how="left")

        result["utr"] = result[["tvr_std_20_rk", "tvr_mean_20_mod"]].mean(axis=1)
        result = result.drop(["tvr_std_20_rk", "tvr_mean_20_mod"], axis=1)

        result["tvr_chg_mean_20"] = group["tvr_chg"].mean()
        result["tvr_chg_std_20"] = group["tvr_chg"].std()
        result["tvr_chg_msr_20"] = result["tvr_chg_mean_20"].div(result["tvr_chg_std_20"])

        # 振幅
        result["amp_mean_20"] = group["amplitude"].mean()
        result["amp_std_20"] = group["amplitude"].std()
        result["amp_msr_20"] = result["amp_mean_20"].div(result["amp_std_20"])

        # 影线差
        result["shadow_diff_mean_20"] = group["shadow_diff"].mean()
        result["shadow_diff_std_20"] = group["shadow_diff"].std()
        result["shadow_diff_msr_20"] = result["shadow_diff_mean_20"].div(result["shadow_diff_std_20"])

        # PLUS
        result["plus_mean_20"] = group["plus"].mean()
        result["plus_std_20"] = group["plus"].std()
        result["plus_msr_20"] = result["plus_mean_20"].div(result["plus_std_20"])

        # 非流动性
        result["illiq_mean_20"] = group["illiq"].mean()
        result["illiq_std_20"] = group["illiq"].std()
        result["illiq_msr_20"] = result["illiq_mean_20"].div(result["illiq_std_20"])

        # 高子剑研报
        result["tvr_deplus_mean_20"] = group["tvr_deplus"].mean()
        result["tvr_deplus_std_20"] = group["tvr_deplus"].std()
        result["plus_detvr_mean_20"] = group["plus_detvr"].mean()
        result["plus_detvr_std_20"] = group["plus_detvr"].std()

        result["tvr_ne_deplus_mean_20"] = group["tvr_ne_deplus"].mean()
        result["tvr_ne_deplus_std_20"] = group["tvr_ne_deplus"].std()
        result["plus_ne_detvr_mean_20"] = group["plus_ne_detvr"].mean()
        result["plus_ne_detvr_std_20"] = group["plus_ne_detvr"].std()

        # 魏建榕研报
        result["sm_imba_norm"] = group["net_sm_vol"].sum().div(group["abs_net_sm_vol"].sum())
        result["sm_imba_deret"] = orthogonalize_factor(
            result, target_factor="sm_imba_norm", control_factors=["ret_20"], date_col="trade_date"
        )
        result["ret_de_sm_imba"] = orthogonalize_factor(
            result, target_factor="ret_20", control_factors=["sm_imba_norm"], date_col="trade_date"
        )
        result["elg_imba_norm"] = group["net_elg_vol"].sum().div(group["abs_net_elg_vol"].sum())
        result["elg_imba_deret"] = orthogonalize_factor(
            result, target_factor="elg_imba_norm", control_factors=["ret_20"], date_col="trade_date"
        )
        result["ret_de_elg_imba"] = orthogonalize_factor(
            result, target_factor="ret_20", control_factors=["elg_imba_norm"], date_col="trade_date"
        )

        result = result.reset_index()
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y%m%d")
        return result
