"""未来 N 日收益率标签（个股 + ETF + 指数 统一）。

表名：label_forward_returns（基类自动加 label_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：overwrite
partition_col：trade_date

数据源：panel_stock_daily（个股） + panel_fund_daily（ETF，含 fund_adj 复权因子）
       + index_daily（宽基+申万行业指数，无复权无停牌）
asset_type 列区分：stock / etf / index

----
设计决策（2026-07-22 重构）：
1. 交易日历回推（非 per-stock rolling）：所有 ts_code 用同一个 trade_date -> trade_date+N 映射。
   停牌日 forward-fill adj_close + 标记 is_suspend_N。这是私募标准做法，保证横截面可比。
2. 公式：forward_return_N = adj_close[T+1+N] / adj_close[T+1] - 1
   T+1 买入（避免未来函数），持有 N 个交易日后 T+1+N 卖出。
3. N in {1, 2, 3, 5, 10, 20, 40, 60}。
4. 更新策略：每天回跑 T-61 ~ T，partition_col = trade_date，overwrite 覆盖。
5. 三类资产同一张表 + asset_type 列区分。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.dates import get_next_n_trading_date
from data.label.base import LabelCalculator

logger = logging.getLogger(__name__)

# 未来收益率窗口（交易日）
FORWARD_WINDOWS = [1, 2, 3, 5, 10, 20, 40, 60]
# 最大窗口（交易日）
MAX_WINDOW = FORWARD_WINDOWS[-1]
# 读数据前向扩展窗口缓冲（交易日）：MAX_WINDOW + 10 天余量
READ_BUFFER_TRADING_DAYS = MAX_WINDOW + 10


class ForwardReturnsCalculator(LabelCalculator):
    """未来 N 日收益率标签计算器。

    从 panel_stock_daily 取 adj_close（= close * adj_factor），
    按交易日历回推计算 forward_return_N = adj_close[T+1+N] / adj_close[T+1] - 1。
    停牌日 forward-fill 价格 + 标记 is_suspend_N。
    """

    table_name = "forward_returns"
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    output_schema = {
        "ts_code": "string",
        "trade_date": "date",
        "asset_type": "string",
        "fwd_ret_1d": "float",
        "fwd_ret_2d": "float",
        "fwd_ret_3d": "float",
        "fwd_ret_5d": "float",
        "fwd_ret_10d": "float",
        "fwd_ret_20d": "float",
        "fwd_ret_40d": "float",
        "fwd_ret_60d": "float",
        "is_suspend_1d": "int",
        "is_suspend_2d": "int",
        "is_suspend_3d": "int",
        "is_suspend_5d": "int",
        "is_suspend_10d": "int",
        "is_suspend_20d": "int",
        "is_suspend_40d": "int",
        "is_suspend_60d": "int",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("ForwardReturnsCalculator 初始化完成")

    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """取 panel_stock_daily + panel_fund_daily + index_daily 数据。

        end_date 向前扩展 READ_BUFFER_TRADING_DAYS 个交易日以覆盖最远 horizon 的未来价格。
        start_date/end_date 可能是 YYYY-MM-DD 或 YYYYMMDD 格式。
        """
        if start_date:
            start_date = start_date.replace("-", "")
        if end_date:
            end_date = end_date.replace("-", "")

        # 向前扩展 end_date 以覆盖最远 horizon 的未来价格
        read_end = (
            get_next_n_trading_date(end_date, READ_BUFFER_TRADING_DAYS)
            if end_date
            else None
        )

        date_clause = ""
        if start_date:
            date_clause += f" AND trade_date >= '{start_date}'"
        if read_end:
            date_clause += f" AND trade_date <= '{read_end}'"

        query = f"""
        SELECT ts_code, trade_date, close, adj_factor, is_suspend, 'stock' AS asset_type
        FROM panel_stock_daily
        WHERE 1=1 {date_clause}
        UNION ALL
        SELECT ts_code, trade_date, close, adj_factor, is_suspend, 'etf' AS asset_type
        FROM panel_fund_daily
        WHERE 1=1 {date_clause}
        UNION ALL
        SELECT ts_code, trade_date, close, 1.0 AS adj_factor, 0 AS is_suspend, 'index' AS asset_type
        FROM index_daily
        WHERE 1=1 {date_clause}
        """

        self.logger.info(
            f"取 panel_stock_daily + panel_fund_daily + index_daily: "
            f"{start_date or '开始'}~{read_end or '结束'}"
        )
        return pd.read_sql(query, self.engine)

    def process_data(
        self,
        data: pd.DataFrame,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """计算未来 N 日收益率标签。

        步骤：
        1. 计算 adj_close = close * adj_factor
        2. 构建 (trade_date x ts_code) 全网格，停牌日 forward-fill
        3. pivot 为矩阵，按交易日历 shift 计算各 horizon 收益率
        4. 过滤到 [start_date, end_date] 输出区间
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        # ===== Step 1: 计算 adj_close =====
        df = data.copy()
        df["adj_close"] = df["close"] * df["adj_factor"]
        # trade_date 转 datetime 以便比较
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        all_dates = sorted(df["trade_date"].unique())
        all_codes = sorted(df["ts_code"].unique())

        self.logger.info(
            f"[1/4] {len(all_codes)} stocks x {len(all_dates)} dates, "
            f"building full grid for calendar-based forward returns..."
        )

        # ===== Step 2: 构建全网格 + forward-fill =====
        full_idx = pd.MultiIndex.from_product(
            [all_dates, all_codes], names=["trade_date", "ts_code"]
        )
        df_full = (
            df.set_index(["trade_date", "ts_code"])
            .reindex(full_idx)
            .sort_index()
        )

        # 按 ts_code 分组 forward-fill（停牌日沿用上一交易日价格）
        df_full["adj_close"] = df_full.groupby("ts_code")["adj_close"].ffill()
        df_full["is_suspend"] = (
            df_full.groupby("ts_code")["is_suspend"]
            .ffill()
            .fillna(1)
            .astype(int)
        )
        df_full = df_full.reset_index()

        # ===== Step 3: pivot 为矩阵 =====
        # 保存 asset_type 映射（pivot 只支持数值列，需单独保留）
        code_to_type = (
            df[["ts_code", "asset_type"]]
            .drop_duplicates()
            .set_index("ts_code")["asset_type"]
        )

        prices = df_full.pivot(
            index="trade_date", columns="ts_code", values="adj_close"
        )
        suspends = df_full.pivot(
            index="trade_date", columns="ts_code", values="is_suspend"
        )

        self.logger.info(
            f"[2/4] pivot shape: {prices.shape} "
            f"(stocks={len(prices.columns)}, dates={len(prices.index)}), "
            f"computing forward returns..."
        )

        # ===== Step 4: 按交易日历 shift 计算各 horizon =====
        # 入口价格：T+1（shift(-1)）
        entry_prices = prices.shift(-1)

        # 从原始数据取 (ts_code, trade_date) 基础对
        base = df[["ts_code", "trade_date"]].drop_duplicates()

        for N in FORWARD_WINDOWS:
            # 未来价格：T+1+N
            future_prices = prices.shift(-(N + 1))
            ret_matrix = future_prices.values / entry_prices.values - 1

            # 停牌标记：T+1 或 T+1+N 任一停牌 -> 1
            entry_suspend = suspends.shift(-1)
            exit_suspend = suspends.shift(-(N + 1))
            suspend_matrix = (
                (entry_suspend.fillna(1).values == 1)
                | (exit_suspend.fillna(1).values == 1)
            ).astype(int)

            # 转为长表
            ret_df = pd.DataFrame(
                ret_matrix, index=prices.index, columns=prices.columns
            )
            ret_long = ret_df.stack().reset_index()
            ret_long.columns = ["trade_date", "ts_code", f"fwd_ret_{N}d"]

            susp_df = pd.DataFrame(
                suspend_matrix, index=prices.index, columns=prices.columns
            )
            susp_long = susp_df.stack().reset_index()
            susp_long.columns = ["trade_date", "ts_code", f"is_suspend_{N}d"]

            base = base.merge(ret_long, on=["trade_date", "ts_code"], how="left")
            base = base.merge(susp_long, on=["trade_date", "ts_code"], how="left")

        # ===== Step 5: 过滤到输出区间 + 恢复 asset_type =====
        base["asset_type"] = base["ts_code"].map(code_to_type)
        if start_date:
            start_dt = pd.to_datetime(start_date.replace("-", ""), format="%Y%m%d")
            base = base[base["trade_date"] >= start_dt]
        if end_date:
            end_dt = pd.to_datetime(end_date.replace("-", ""), format="%Y%m%d")
            base = base[base["trade_date"] <= end_dt]

        # 统计
        valid_count = base.dropna(
            subset=[f"fwd_ret_{MAX_WINDOW}d"]
        ).shape[0]
        total_count = base.shape[0]
        self.logger.info(
            f"[3/4] output: {total_count} rows, "
            f"{valid_count} with valid fwd_ret_{MAX_WINDOW}d "
            f"({valid_count / max(total_count, 1) * 100:.1f}%), "
            f"date range [{base['trade_date'].min().date()} ~ {base['trade_date'].max().date()}]"
        )

        # NaN 转 None（MySQL NULL）
        base = base.where(pd.notnull(base), None)

        return base