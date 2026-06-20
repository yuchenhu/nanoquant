"""ETF 资产池定义（Phase 1: A股 ETF）。

CLAUDE.md 4.4: Phase 1 纯 A股 ETF（行业/风格/宽基）。
开放问题 9.1: ETF 池范围由作者给定。此处给一个合理默认值，可后续调整。

分类：
- 宽基: 沪深300/中证500/中证1000/创业板/科创50/上证50
- 行业: 消费/医药/银行/券商/半导体/新能源/军工
- 风格: 红利/价值/成长
"""
from __future__ import annotations

from typing import Dict, List

# 默认 ETF 池（ts_code → 名称 + 分类）
# 代码格式遵循 tushare ETF 约定（.SH/.SZ）
DEFAULT_ETF_POOL: Dict[str, Dict[str, str]] = {
    # ===== 宽基 =====
    "510300.SH": {"name": "沪深300ETF", "category": "broad"},
    "510500.SH": {"name": "中证500ETF", "category": "broad"},
    "512100.SH": {"name": "中证1000ETF", "category": "broad"},
    "159915.SZ": {"name": "创业板ETF", "category": "broad"},
    "588000.SH": {"name": "科创50ETF", "category": "broad"},
    "510050.SH": {"name": "上证50ETF", "category": "broad"},
    # ===== 行业 =====
    "159928.SZ": {"name": "消费ETF", "category": "industry"},
    "512010.SH": {"name": "医药ETF", "category": "industry"},
    "512800.SH": {"name": "银行ETF", "category": "industry"},
    "512000.SH": {"name": "券商ETF", "category": "industry"},
    "512480.SH": {"name": "半导体ETF", "category": "industry"},
    "516160.SH": {"name": "新能源ETF", "category": "industry"},
    "512660.SH": {"name": "军工ETF", "category": "industry"},
    # ===== 风格 =====
    "510880.SH": {"name": "红利ETF", "category": "style"},
    "519671.SH": {"name": "价值ETF", "category": "style"},
    "159909.SZ": {"name": "成长ETF", "category": "style"},
}


def get_etf_universe(category: str = "all") -> List[str]:
    """获取 ETF 池代码列表。

    category:
    - all: 全部
    - broad / industry / style: 按分类筛选
    """
    if category == "all":
        return list(DEFAULT_ETF_POOL.keys())
    return [
        code for code, info in DEFAULT_ETF_POOL.items()
        if info["category"] == category
    ]
