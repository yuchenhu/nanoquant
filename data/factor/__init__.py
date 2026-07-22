"""Factor 计算层（data/factor/）。

设计（CLAUDE.md 2.2 / 2.5 / 2.6）：
- 所有因子继承 FactorCalculator（自动加 factor_ 表名前缀）
- 每个因子声明 output_schema + biz_date_col + primary_keys
- 统一 update(start_date, end_date, **params) 入口

注册表 CALCULATORS：供 scripts/run_compute.py 调度。
"""
from data.factor.base import FactorCalculator
from data.factor.high_low_spread import HighLowSpreadCalculator
from data.factor.industry_resonance import IndustryResonanceCalculator
from data.factor.moneyflow_imbalance import MoneyFlowImbalanceCalculator
from data.factor.price_volume_20d import PriceVolume20DCalculator
from data.factor.trader_structure import TraderStructureCalculator
from data.factor.valuation import ValuationCalculator

# Factor 计算器注册表（name → class）
CALCULATORS = {
    "high_low_spread": HighLowSpreadCalculator,
    "industry_resonance": IndustryResonanceCalculator,
    "moneyflow_imbalance": MoneyFlowImbalanceCalculator,
    "price_volume_20d": PriceVolume20DCalculator,
    "trader_structure": TraderStructureCalculator,
    "valuation": ValuationCalculator,
}

__all__ = [
    "FactorCalculator",
    "HighLowSpreadCalculator",
    "IndustryResonanceCalculator",
    "MoneyFlowImbalanceCalculator",
    "PriceVolume20DCalculator",
    "TraderStructureCalculator",
    "ValuationCalculator",
    "CALCULATORS",
]
