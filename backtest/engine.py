"""向量化回测引擎（MVP，无外部依赖）。

逻辑：
1. 按调仓频率（默认周频）遍历交易日
2. 每个调仓日调用 strategy.compute_target_weights 得目标权重
3. 持有到下一调仓日，按日收益率计算组合收益
4. 输出净值曲线 + 调仓记录 + 指标

与 signals/ 共用：signals 复用 strategy.compute_target_weights 生成最新信号。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from backtest.metrics import compute_metrics
from config.database import execute_sql

logger = logging.getLogger(__name__)


class VectorizedBacktester:
    """向量化回测引擎。

    参数：
    - strategy: 策略对象（需有 compute_target_weights 方法）
    - rebalance_freq: 调仓频率（'W'=周, 'M'=月, 'D'=日）
    - commission: 单边手续费（默认 0.0005）
    """

    def __init__(
        self,
        strategy,
        rebalance_freq: str = "W",
        commission: float = 0.0005,
    ):
        self.strategy = strategy
        self.rebalance_freq = rebalance_freq
        self.commission = commission

    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表（从 trade_cal 取 is_open=1）。"""
        sql = f"""
            SELECT cal_date
            FROM trade_cal
            WHERE is_open = 1
              AND cal_date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY cal_date
        """
        df = execute_sql(sql)
        return df["cal_date"].tolist() if not df.empty else []

    def get_returns_data(
        self, start_date: str, end_date: str, codes: List[str]
    ) -> pd.DataFrame:
        """取 ETF 日收益率（pivot: trade_date × ts_code）。"""
        code_list = ",".join(f"'{c}'" for c in codes)
        sql = f"""
            SELECT ts_code, trade_date, close
            FROM panel_stock_daily
            WHERE ts_code IN ({code_list})
              AND trade_date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY ts_code, trade_date
        """
        df = execute_sql(sql)
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot(index="trade_date", columns="ts_code", values="close")
        return pivot.pct_change().fillna(0.0)

    def is_rebalance_day(self, date: str, dates: List[str]) -> bool:
        """判断是否为调仓日（按频率）。"""
        idx = dates.index(date)
        if idx == 0:
            return True
        prev = dates[idx - 1]
        # 周频：跨周；月频：跨月
        if self.rebalance_freq == "W":
            return pd.Timestamp(date).week != pd.Timestamp(prev).week
        elif self.rebalance_freq == "M":
            return pd.Timestamp(date).month != pd.Timestamp(prev).month
        return True  # 日频

    def run(
        self, start_date: str, end_date: str
    ) -> Dict[str, pd.DataFrame]:
        """运行回测。

        返回：
        - equity_curve: 净值曲线（trade_date, nav, daily_return）
        - trades: 调仓记录（trade_date, ts_code, weight）
        - metrics: 指标 dict
        """
        codes = self.strategy.get_universe()
        dates = self.get_trading_dates(start_date, end_date)
        if not dates:
            logger.error("无交易日，回测终止")
            return {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "metrics": {}}

        returns_df = self.get_returns_data(start_date, end_date, codes)
        if returns_df.empty:
            logger.error("无行情数据，回测终止")
            return {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "metrics": {}}

        nav = 1.0
        current_weights: Dict[str, float] = {}
        equity_records: List[Dict] = []
        trade_records: List[Dict] = []
        peak_nav = 1.0

        for date in dates:
            if date not in returns_df.index:
                continue
            day_returns = returns_df.loc[date]

            # 调仓
            if self.is_rebalance_day(date, dates):
                drawdown = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0.0
                new_weights = self.strategy.compute_target_weights(
                    end_date=date, current_drawdown=drawdown
                )
                new_weights = self.strategy.apply_constraints(new_weights)

                # 手续费（换手部分）
                turnover = sum(
                    abs(new_weights.get(c, 0) - current_weights.get(c, 0))
                    for c in set(new_weights) | set(current_weights)
                )
                nav *= (1 - self.commission * turnover)
                current_weights = new_weights

                for c, w in current_weights.items():
                    trade_records.append({
                        "trade_date": date, "ts_code": c, "weight": w, "nav": nav,
                    })

            # 当日组合收益
            port_return = sum(
                current_weights.get(c, 0) * day_returns.get(c, 0.0)
                for c in current_weights
            )
            nav *= (1 + port_return)
            peak_nav = max(peak_nav, nav)
            equity_records.append({
                "trade_date": date, "nav": nav, "daily_return": port_return,
            })

        equity_curve = pd.DataFrame(equity_records).set_index("trade_date")
        trades = pd.DataFrame(trade_records)
        metrics = compute_metrics(equity_curve["daily_return"]) if not equity_curve.empty else {}

        logger.info(
            f"回测完成: {start_date}~{end_date}, "
            f"总收益={metrics.get('total_return', 0):.2%}, "
            f"夏普={metrics.get('sharpe', 0):.2f}, "
            f"最大回撤={metrics.get('max_drawdown', 0):.2%}"
        )
        return {"equity_curve": equity_curve, "trades": trades, "metrics": metrics}
