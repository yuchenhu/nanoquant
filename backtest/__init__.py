"""backtest/: 回测引擎层（向量化回测 + 指标计算）。

模块：
- metrics.py: compute_metrics（年化、夏普、最大回撤、胜率）
- engine.py: VectorizedBacktester（向量化回测，输出净值/调仓/指标）

CLAUDE.md 5.2: 回测引擎用 backtesting.py。MVP 先用轻量向量化回测
（无外部依赖，Python 3.14 兼容），后续可替换为 backtesting.py。

与 portfolio/ + signals/ 共用策略代码：engine 调用 strategy.compute_target_weights。
"""
from backtest.engine import VectorizedBacktester
from backtest.metrics import compute_metrics

__all__ = ["VectorizedBacktester", "compute_metrics"]
