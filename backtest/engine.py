"""向量化回测引擎（真实成本 + 防未来函数）。

交易成本（华泰涨乐财富通 ETF 费率，2025-2026）：
  - 佣金: 万2.5 双向，最低5元/笔（10万/笔时25元>5元，不触发最低）
  - 滑点: 0.1% 单边（保守，覆盖流动性较差的行业ETF）
  - 往返合计: ~0.25% per unit turnover
  - ETF 免印花税、免过户费

防未来函数：
  - T日收盘后计算信号 → T+1日起新权重生效
  - T日收益用旧权重，T+1日起用新权重
  - 换手成本在T日扣除（模拟T+1开盘执行的滑点+佣金）

停牌过滤：
  - 调仓日检查标的是否停牌，停牌标的跳过（不买也不卖）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import pandas as pd

from backtest.metrics import compute_metrics
from config.database import execute_sql

logger = logging.getLogger(__name__)


@dataclass
class CostConfig:
    """交易成本配置。

    华泰涨乐财富通 ETF 实际费率：
    - commission_rate: 万2.5（0.00025），可找客户经理谈到万1.5~万2
    - min_commission: 5元/笔，10万以上基本不触发
    - slippage: 0.1% 单边滑点（保守估计）
    - 往返合计 = (0.025%+0.1%)*2 = 0.25% per unit turnover
    """
    commission_rate: float = 0.00025   # 万2.5 佣金
    min_commission: float = 5.0         # 最低5元/笔
    slippage: float = 0.001             # 0.1% 单边滑点


class VectorizedBacktester:
    """向量化回测引擎（真实成本版）。

    参数：
    - strategy: 策略对象（需有 compute_target_weights 方法）
    - rebalance_freq: 调仓频率（'W'=周, 'M'=月, 'D'=日）
    - cost_config: 交易成本配置（默认华泰涨乐财富通 ETF 费率）
    - capital: 初始资金（元，默认 100万）
    - filter_suspend: 是否过滤停牌标的（默认 True）
    """

    def __init__(
        self,
        strategy,
        rebalance_freq: str = "W",
        cost_config: Optional[CostConfig] = None,
        capital: float = 1_000_000.0,
        filter_suspend: bool = True,
    ):
        self.strategy = strategy
        self.rebalance_freq = rebalance_freq
        self.cost_config = cost_config or CostConfig()
        self.capital = capital
        self.filter_suspend = filter_suspend

    # ============================================================
    # 数据获取
    # ============================================================

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
        """取 ETF 日收益率矩阵（pivot: trade_date × ts_code）。

        用 close-to-close 日收益率，回测主循环中持有期收益用此矩阵。
        调仓日成本单独扣除（见 _calc_turnover_cost）。
        """
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

    def get_suspend_data(
        self, start_date: str, end_date: str, codes: List[str]
    ) -> pd.DataFrame:
        """取停牌数据，返回每个交易日的停牌代码集合。

        返回 DataFrame: trade_date × suspend_codes（逗号分隔字符串）。
        停牌判断：suspend_date <= trade_date <= resume_date（resume_date 为 NULL 则至今停牌）。
        """
        if not self.filter_suspend:
            return pd.DataFrame()
        code_list = ",".join(f"'{c}'" for c in codes)
        sql = f"""
            SELECT ts_code, suspend_date,
                   COALESCE(resume_date, '20991231') AS resume_date
            FROM suspend
            WHERE ts_code IN ({code_list})
              AND suspend_date <= '{end_date}'
              AND (resume_date IS NULL OR resume_date >= '{start_date}')
        """
        df = execute_sql(sql)
        if df.empty:
            return pd.DataFrame()
        return df

    # ============================================================
    # 调仓判断
    # ============================================================

    def is_rebalance_day(self, date: str, dates: List[str]) -> bool:
        """判断是否为调仓日（按频率）。"""
        idx = dates.index(date)
        if idx == 0:
            return True
        prev = dates[idx - 1]
        if self.rebalance_freq == "W":
            return pd.Timestamp(date).week != pd.Timestamp(prev).week
        elif self.rebalance_freq == "M":
            return pd.Timestamp(date).month != pd.Timestamp(prev).month
        return True  # 日频

    # ============================================================
    # 停牌过滤
    # ============================================================

    def _get_suspended_codes(self, date: str, suspend_df: pd.DataFrame) -> Set[str]:
        """返回指定日期停牌的代码集合。"""
        if suspend_df.empty:
            return set()
        suspended = suspend_df[
            (suspend_df["suspend_date"] <= date)
            & (suspend_df["resume_date"] >= date)
        ]
        return set(suspended["ts_code"].tolist())

    # ============================================================
    # 交易成本
    # ============================================================

    def _calc_turnover_cost(
        self,
        old_weights: Dict[str, float],
        new_weights: Dict[str, float],
        nav: float,
    ) -> float:
        """计算换手成本（元）。

        sum(|new - old|) 是双边权重变化（买+卖各计一次）。
        买入金额 = 卖出金额 = nav * sum(|new-old|) / 2。
        成本 = (买入金额 + 卖出金额) * (佣金率 + 滑点率)
             = nav * sum(|new-old|) * (佣金率 + 滑点率)

        返回: 成本金额（元），从 nav 中扣除。
        """
        all_codes = set(old_weights) | set(new_weights)
        total_weight_change = sum(
            abs(new_weights.get(c, 0.0) - old_weights.get(c, 0.0))
            for c in all_codes
        )
        if total_weight_change == 0:
            return 0.0

        # 交易总金额 = nav * sum(|new-old|)（买+卖合计）
        trade_amount = nav * total_weight_change

        # 佣金（最低5元/笔，但10万+基本不触发）
        commission = max(
            trade_amount * self.cost_config.commission_rate,
            self.cost_config.min_commission,
        )

        # 滑点
        slippage_cost = trade_amount * self.cost_config.slippage

        total_cost = commission + slippage_cost
        logger.debug(
            f"换手={total_weight_change:.2%}, "
            f"交易金额={trade_amount:.0f}, "
            f"佣金={commission:.1f}, "
            f"滑点={slippage_cost:.1f}, "
            f"合计={total_cost:.1f}"
        )
        return total_cost

    # ============================================================
    # 主循环
    # ============================================================

    def run(
        self, start_date: str, end_date: str
    ) -> Dict[str, pd.DataFrame]:
        """运行回测。

        时序：
        - T日收盘后计算信号（compute_target_weights）
        - T日收益用旧权重
        - T日扣除换手成本
        - T+1日起新权重生效

        返回：
        - equity_curve: 净值曲线（trade_date, nav, daily_return）
        - trades: 调仓记录（trade_date, ts_code, weight）
        - metrics: 指标 dict
        """
        codes = self.strategy.get_universe()
        dates = self.get_trading_dates(start_date, end_date)
        if not dates:
            logger.error("无交易日，回测终止")
            return {
                "equity_curve": pd.DataFrame(),
                "trades": pd.DataFrame(),
                "metrics": {},
            }

        returns_df = self.get_returns_data(start_date, end_date, codes)
        if returns_df.empty:
            logger.error("无行情数据，回测终止")
            return {
                "equity_curve": pd.DataFrame(),
                "trades": pd.DataFrame(),
                "metrics": {},
            }

        suspend_df = self.get_suspend_data(start_date, end_date, codes)

        nav = self.capital
        current_weights: Dict[str, float] = {}
        equity_records: List[Dict] = []
        trade_records: List[Dict] = []
        peak_nav = nav

        for date in dates:
            if date not in returns_df.index:
                continue
            day_returns = returns_df.loc[date]

            # ---- 当日收益：用当前持仓权重（上一调仓日确定） ----
            port_return = 0.0
            if current_weights:
                port_return = sum(
                    current_weights.get(c, 0.0) * day_returns.get(c, 0.0)
                    for c in current_weights
                )
            nav *= (1.0 + port_return)
            peak_nav = max(peak_nav, nav)
            equity_records.append({
                "trade_date": date,
                "nav": round(nav, 2),
                "daily_return": round(port_return, 6),
            })

            # ---- 收盘后：判断是否调仓 ----
            if not self.is_rebalance_day(date, dates):
                continue

            # 停牌过滤：调仓日跳过停牌标的
            suspended = self._get_suspended_codes(date, suspend_df)
            if suspended:
                logger.info(f"{date} 停牌标的(跳过): {suspended}")

            drawdown = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0.0
            new_weights = self.strategy.compute_target_weights(
                end_date=date, current_drawdown=drawdown
            )
            new_weights = self.strategy.apply_constraints(new_weights)

            # 过滤停牌标的（不买也不卖，保留原仓位）
            if suspended:
                for c in list(new_weights.keys()):
                    if c in suspended:
                        logger.info(f"{date} 跳过停牌标的: {c}")
                        del new_weights[c]
                # 重新归一化
                total = sum(new_weights.values())
                if total > 0:
                    new_weights = {c: w / total for c, w in new_weights.items()}

            # 换手成本
            cost = self._calc_turnover_cost(current_weights, new_weights, nav)
            nav -= cost

            # 记录调仓
            for c, w in new_weights.items():
                trade_records.append({
                    "trade_date": date,
                    "ts_code": c,
                    "weight": round(w, 4),
                    "nav": round(nav, 2),
                })

            current_weights = new_weights

        # ---- 汇总 ----
        equity_curve = pd.DataFrame(equity_records).set_index("trade_date")
        trades = pd.DataFrame(trade_records)
        metrics = compute_metrics(
            equity_curve["daily_return"]
        ) if not equity_curve.empty else {}

        logger.info(
            f"回测完成: {start_date}~{end_date}, "
            f"总收益={metrics.get('total_return', 0):.2%}, "
            f"夏普={metrics.get('sharpe', 0):.2f}, "
            f"最大回撤={metrics.get('max_drawdown', 0):.2%}"
        )
        return {
            "equity_curve": equity_curve,
            "trades": trades,
            "metrics": metrics,
        }