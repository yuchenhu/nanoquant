# -*- coding: utf-8 -*-
"""smart beta 家族盘点（除红利/现金流外）：低波/成长/价值/质量/动量/基本面/股东回报。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)

# 0) 全收益 vs 价格对照：中证红利 2026-07-09
cmp_tr = execute_sql(
    "SELECT ts_code, close FROM index_daily WHERE trade_date='2026-07-09' "
    "AND ts_code IN ('000922.CSI','930955.CSI')"
)
print("[OK] price-index closes 2026-07-09 (local):")
print(cmp_tr.to_string(index=False) if not cmp_tr.empty else "[EMPTY]")
print("[REF] H00922.CSI (TR) same day = 11138.6073 (tushare probe)")

# 1) index_basic 家族清单（主流发布商，剔除港/债/全收益/净收益/主题）
for kw in ["低波", "成长", "动量", "基本面", "价值", "股东回报"]:
    df = execute_sql(
        "SELECT ts_code, name, market, base_date, list_date FROM index_basic "
        f"WHERE name LIKE '%{kw}%' AND market IN ('CSI','SSE','CNI','SZSE') "
        "AND ts_code NOT LIKE '%CNY%' AND name NOT LIKE '%港%' AND name NOT LIKE '%债%' "
        "AND name NOT LIKE '%USD%' AND name NOT LIKE '%HKD%' "
        "AND (name LIKE '%300%' OR name LIKE '%500%' OR name LIKE '%800%' OR name LIKE '%1000%' "
        "OR name LIKE '%50%' OR name LIKE '%100%' OR name LIKE '%A股%' OR name LIKE '%低波动%') "
        "ORDER BY list_date LIMIT 25"
    )
    df = df[~df["name"].str.contains("增强|等权|质量成长|ESG|长三角|大湾区|价值稳健|成长低波", na=False)]
    print(f"\n[OK] index_basic kw='{kw}':", "NONE" if df.empty else len(df))
    if not df.empty:
        print(df.to_string(index=False))

# 2) 对应 ETF 存在性（主流风格 ETF 命中）
for kw in ["低波动", "价值ETF", "成长ETF", "质量ETF", "基本面", "动量", "股东回报"]:
    m = execute_sql(
        "SELECT ts_code, name, list_date, delist_date FROM fund_basic "
        f"WHERE market='E' AND name LIKE '%{kw}%' AND delist_date IS NULL "
        "ORDER BY list_date LIMIT 12"
    )
    print(f"\n[OK] ETF kw='{kw}':", "NONE" if m.empty else len(m))
    if not m.empty:
        print(m.to_string(index=False))
