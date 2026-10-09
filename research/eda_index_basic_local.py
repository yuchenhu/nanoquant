# -*- coding: utf-8 -*-
"""本地 index_basic 盘点：候选梯度代码的基础信息是否已在本地（同步于最近一次 etl）。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 220)

CANDIDATES = [
    "000001.SH", "000016.SH", "930050.CSI", "000010.SH", "000009.SH",
    "000300.SH", "000510.CSI", "000510.SH", "399004.SZ", "399001.SZ",
    "000905.SH", "000906.SH", "000852.SH", "932000.CSI", "399303.SZ",
    "000688.SH", "000698.SH", "000680.SH", "399006.SZ", "399673.SZ",
    "399102.SZ", "899050.BJ", "000985.CSI", "8841431.WI",
]

q = "','".join(CANDIDATES)
df = execute_sql(
    f"SELECT ts_code, name, market, publisher, category, base_date, base_point, list_date "
    f"FROM index_basic WHERE ts_code IN ('{q}') ORDER BY ts_code"
)
print("[OK] local index_basic rows for candidates:", len(df))
print(df.to_string(index=False))

missing = [c for c in CANDIDATES if c not in set(df["ts_code"])]
print("\n[EMPTY] candidates NOT in local index_basic:", missing)

# 顺带：本地 index_basic 总量与市场分布（判断 etl 是否全量拉过）
tot = execute_sql(
    "SELECT market, COUNT(*) AS n FROM index_basic GROUP BY market ORDER BY n DESC"
)
print("\n[OK] local index_basic by market:")
print(tot.to_string(index=False))

# 微盘股关键词兜底：万一 Wind 代码不同
micro = execute_sql(
    "SELECT ts_code, name, market, publisher FROM index_basic WHERE name LIKE '%微盘%'"
)
print("\n[OK] name LIKE '%微盘%' in local index_basic:", "NONE" if micro.empty else "")
if not micro.empty:
    print(micro.to_string(index=False))
