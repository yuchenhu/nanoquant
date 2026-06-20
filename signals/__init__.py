"""signals/: 调仓信号生成层（与回测共用策略逻辑）。

模块：
- generator.py: SignalGenerator（复用 portfolio.strategy 生成最新调仓信号）

CLAUDE.md: signals/ 与 backtest/ 共用同一套策略代码，避免回测/实盘两套。
SignalGenerator 调用 strategy.compute_target_weights，输出 signal_rebalance 表。
"""
from signals.generator import SignalGenerator

__all__ = ["SignalGenerator"]
