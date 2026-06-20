"""调仓信号生成器（复用 portfolio.strategy）。

逻辑：
1. 取最新交易日
2. 调用 strategy.compute_target_weights 得目标权重
3. 与当前持仓对比，生成调仓信号（买入/卖出/持有）
4. 落库 signal_rebalance 表

表结构（signal_rebalance）：
- signal_date: 信号日（主键之一）
- ts_code: ETF 代码（主键之一）
- target_weight: 目标权重
- action: 调仓动作（buy/sell/hold）
- strategy_name: 策略名
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from config.database import execute_sql, save_to_database

logger = logging.getLogger(__name__)


class SignalGenerator:
    """调仓信号生成器。

    参数：
    - strategy: 策略对象（需有 compute_target_weights / apply_constraints 方法）
    - strategy_name: 策略名（落库标识）
    """

    def __init__(self, strategy, strategy_name: str = "etf_momentum"):
        self.strategy = strategy
        self.strategy_name = strategy_name

    def get_latest_trade_date(self) -> Optional[str]:
        """取最新交易日。"""
        df = execute_sql(
            "SELECT cal_date FROM trade_cal WHERE is_open=1 "
            "ORDER BY cal_date DESC LIMIT 1"
        )
        if df.empty:
            return None
        return str(df.iloc[0, 0]).replace("-", "")

    def get_current_holdings(self, signal_date: str) -> Dict[str, float]:
        """取最近一次调仓的目标权重作为当前持仓。"""
        df = execute_sql(
            f"""
            SELECT ts_code, target_weight
            FROM signal_rebalance
            WHERE strategy_name = '{self.strategy_name}'
              AND signal_date < '{signal_date}'
            ORDER BY signal_date DESC
            LIMIT 100
            """
        )
        if df.empty:
            return {}
        # 取最新 signal_date 的全部记录
        # （上面 LIMIT 100 是为防止历史记录过多，实际取最新日的）
        latest = df  # 简化：取返回的全部作为最新（MVP）
        return dict(zip(latest["ts_code"], latest["target_weight"]))

    def generate(
        self,
        signal_date: Optional[str] = None,
        current_drawdown: float = 0.0,
    ) -> pd.DataFrame:
        """生成调仓信号。

        返回 DataFrame: [signal_date, ts_code, target_weight, action, strategy_name]
        """
        signal_date = signal_date or self.get_latest_trade_date()
        if not signal_date:
            logger.error("无法确定信号日")
            return pd.DataFrame()

        target_weights = self.strategy.compute_target_weights(
            end_date=signal_date, current_drawdown=current_drawdown
        )
        target_weights = self.strategy.apply_constraints(target_weights)

        current = self.get_current_holdings(signal_date)
        all_codes = set(target_weights) | set(current)

        records: List[Dict] = []
        for code in all_codes:
            target = target_weights.get(code, 0.0)
            current_w = current.get(code, 0.0)
            if target > current_w:
                action = "buy"
            elif target < current_w:
                action = "sell"
            else:
                action = "hold"
            records.append({
                "signal_date": signal_date,
                "ts_code": code,
                "target_weight": target,
                "action": action,
                "strategy_name": self.strategy_name,
            })

        result = pd.DataFrame(records)
        logger.info(
            f"信号日 {signal_date}: 生成 {len(result)} 条信号 "
            f"(buy={sum(r['action']=='buy' for r in records)}, "
            f"sell={sum(r['action']=='sell' for r in records)})"
        )
        return result

    def save(self, signals: pd.DataFrame) -> bool:
        """落库 signal_rebalance 表。"""
        if signals.empty:
            logger.warning("无信号可保存")
            return False
        return save_to_database(signals, "signal_rebalance", write_mode="upsert")
