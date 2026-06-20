"""ETF 截面轮动策略（CLAUDE.md 4.2 主战场）。

逻辑：
1. 截面动量排序：对池内 ETF 按过去 N 日动量排序
2. 波动率过滤：剔除近 M 日波动率过高的标的
3. 风控约束：
   - 单标的仓位上限（默认 30%）
   - 持仓数量上限（默认 5）
   - 组合回撤止损（默认 10%，触发后空仓）
4. 等权配置入选标的（MVP 简化，后续可加动量加权）

与 backtest/ + signals/ 共用：三者都调用 strategy.compute_target_weights(date)。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from config.database import execute_sql
from portfolio.universe import get_etf_universe

logger = logging.getLogger(__name__)


class CrossSectionalMomentumStrategy:
    """ETF 截面动量轮动策略。

    参数：
    - lookback: 动量回看窗口（交易日，默认 20）
    - vol_window: 波动率计算窗口（默认 20）
    - vol_threshold: 波动率过滤阈值（年化，默认 0.4）
    - max_positions: 持仓数量上限（默认 5）
    - max_weight: 单标的仓位上限（默认 0.3）
    - max_drawdown: 组合回撤止损线（默认 0.10）
    """

    def __init__(
        self,
        lookback: int = 20,
        vol_window: int = 20,
        vol_threshold: float = 0.4,
        max_positions: int = 5,
        max_weight: float = 0.3,
        max_drawdown: float = 0.10,
        universe_category: str = "all",
    ):
        self.lookback = lookback
        self.vol_window = vol_window
        self.vol_threshold = vol_threshold
        self.max_positions = max_positions
        self.max_weight = max_weight
        self.max_drawdown = max_drawdown
        self.universe_category = universe_category

    def get_universe(self) -> List[str]:
        """获取 ETF 池。"""
        return get_etf_universe(self.universe_category)

    def get_price_data(
        self, end_date: str, codes: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """从 panel_stock_daily 取 ETF 日线（close, ts_code, trade_date）。

        MVP 假设 ETF 行情已落入 panel_stock_daily（ts_code 为 ETF 代码）。
        """
        codes = codes or self.get_universe()
        code_list = ",".join(f"'{c}'" for c in codes)
        sql = f"""
            SELECT ts_code, trade_date, close
            FROM panel_stock_daily
            WHERE ts_code IN ({code_list})
              AND trade_date <= '{end_date}'
            ORDER BY ts_code, trade_date
        """
        return execute_sql(sql)

    def compute_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算截面动量（过去 lookback 日收益率）。"""
        pivot = df.pivot(index="trade_date", columns="ts_code", values="close")
        momentum = pivot.pct_change(self.lookback).iloc[-1]
        return momentum.dropna()

    def compute_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算年化波动率（过去 vol_window 日）。"""
        pivot = df.pivot(index="trade_date", columns="ts_code", values="close")
        daily_ret = pivot.pct_change()
        vol = daily_ret.rolling(self.vol_window).std().iloc[-1] * (252 ** 0.5)
        return vol.dropna()

    def compute_target_weights(
        self,
        end_date: str,
        current_drawdown: float = 0.0,
    ) -> Dict[str, float]:
        """计算目标权重。

        返回 {ts_code: weight}，空仓时返回 {}。

        风控：
        - 组合回撤超 max_drawdown → 空仓
        - 波动率超 vol_threshold 的标的剔除
        - 动量排序取前 max_positions
        - 单标的仓位上限 max_weight，剩余仓位按比例分配给其他标的
        """
        # 回撤止损
        if current_drawdown >= self.max_drawdown:
            logger.warning(
                f"组合回撤 {current_drawdown:.2%} >= 止损线 {self.max_drawdown:.2%}，空仓"
            )
            return {}

        codes = self.get_universe()
        df = self.get_price_data(end_date, codes)
        if df.empty:
            logger.warning(f"{end_date} 无 ETF 行情数据")
            return {}

        momentum = self.compute_momentum(df)
        volatility = self.compute_volatility(df)

        # 波动率过滤
        valid_codes = [
            c for c in momentum.index
            if c in volatility.index and volatility[c] <= self.vol_threshold
        ]
        if not valid_codes:
            logger.warning(f"{end_date} 波动率过滤后无标的")
            return {}

        # 动量排序取前 N
        ranked = momentum[valid_codes].sort_values(ascending=False)
        selected = ranked.head(self.max_positions).index.tolist()

        # 等权 + 单标的仓位上限
        raw_weight = 1.0 / len(selected)
        weight = min(raw_weight, self.max_weight)
        total = weight * len(selected)
        # 若总仓位 < 1，剩余仓位按比例补足（不超过 max_weight）
        if total < 1.0:
            remaining = 1.0 - total
            # 给每个标的补 min(remaining/n, max_weight - weight)
            extra_per = min(remaining / len(selected), self.max_weight - weight)
            weight += extra_per

        weights = {c: round(weight, 4) for c in selected}
        logger.info(f"{end_date} 目标持仓: {weights}")
        return weights

    def apply_constraints(
        self, weights: Dict[str, float]
    ) -> Dict[str, float]:
        """应用仓位约束（单标的上限 + 归一化）。"""
        if not weights:
            return {}
        # 单标的上限
        capped = {c: min(w, self.max_weight) for c, w in weights.items()}
        # 归一化
        total = sum(capped.values())
        if total > 0:
            capped = {c: round(w / total, 4) for c, w in capped.items()}
        return capped
