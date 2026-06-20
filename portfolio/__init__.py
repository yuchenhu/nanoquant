"""portfolio/: 组合构建层（ETF 截面轮动、仓位约束）。

模块：
- universe.py: ETF 资产池定义（Phase 1: A股 ETF 宽基+行业+风格）
- strategy.py: CrossSectionalMomentumStrategy（截面动量排序 + 波动率过滤 + 仓位约束）

策略层读加工层表（panel_*/factor_*/label_*），输出目标权重。
与 backtest/ + signals/ 共用同一套策略代码，避免回测/实盘两套。
"""
from portfolio.strategy import CrossSectionalMomentumStrategy
from portfolio.universe import DEFAULT_ETF_POOL, get_etf_universe

__all__ = [
    "CrossSectionalMomentumStrategy",
    "DEFAULT_ETF_POOL",
    "get_etf_universe",
]
