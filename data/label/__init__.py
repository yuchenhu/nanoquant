"""Label 计算层（data/label/）。

设计（CLAUDE.md 2.2 / 2.5 / 2.6）：
- 所有标签继承 LabelCalculator（自动加 label_ 表名前缀）
- 每个标签声明 output_schema + biz_date_col + primary_keys
- 统一 update(start_date, end_date, **params) 入口

注册表 CALCULATORS：供 scripts/run_compute.py 调度。
"""
from data.label.base import LabelCalculator
from data.label.forward_returns import ForwardReturnsCalculator

# Label 计算器注册表（name → class）
CALCULATORS = {
    "forward_returns": ForwardReturnsCalculator,
}

__all__ = [
    "LabelCalculator",
    "ForwardReturnsCalculator",
    "CALCULATORS",
]
