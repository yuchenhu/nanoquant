# -*- coding: utf-8 -*-
"""风格指数完整性 EDA -- 本地 index_basic / index_daily / fund_basic 三连查。

问题：红利、低波、现金流、价值、质量等风格指数，tushare/本地有什么、从何时有、有没有 ETF。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 240)
pd.set_option("display.max_rows", 300)

# 1) index_basic 全库按风格关键词扫描（只看主流发布商：中证 CSI / 上交所 SSE / 深证国证 CNI）
STYLE_KW = ["红利", "低波", "现金流", "价值", "质量", "基本面", "股东回报", "动量", "红利低波"]
frames = []
for kw in STYLE_KW:
    df = execute_sql(
        "SELECT ts_code, name, market, publisher, category, base_date, base_point, list_date "
        f"FROM index_basic WHERE name LIKE '%{kw}%' "
        "AND market IN ('CSI','SSE','CNI','SZSE') "
        "AND (name NOT LIKE '%港%' AND name NOT LIKE '%债%' AND name NOT LIKE '%美%' "
        "AND name NOT LIKE '%策略%' AND name NOT LIKE '%等权%' AND name NOT LIKE '%增强%') "
        "LIMIT 60"
    )
    df["kw"] = kw
    frames.append(df)
sty = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code"])
sty = sty[~sty["name"].str.contains("优选|主题|ESG|500质量|1000质量", na=False)]
print("[OK] style-like indices in local index_basic:", len(sty))
print(sty[["ts_code", "name", "market", "base_date", "list_date", "kw"]]
      .sort_values(["kw", "list_date"]).to_string(index=False))

# 2) 这些风格指数的本地 index_daily 覆盖
codes = "','".join(sty["ts_code"].tolist())
cov = execute_sql(
    f"SELECT ts_code, COUNT(*) AS n, MIN(trade_date) AS d_min, MAX(trade_date) AS d_max "
    f"FROM index_daily WHERE ts_code IN ('{codes}') GROUP BY ts_code"
)
print("\n[OK] local index_daily coverage for style indices:")
print(cov.to_string(index=False) if not cov.empty else "[EMPTY] none")

# 3) 风格 ETF 盘点（红利低波 / 现金流 / 红利 主流宽基风格）
for kw in ["红利低波", "现金流", "红利"]:
    m = execute_sql(
        "SELECT ts_code, name, list_date, delist_date FROM fund_basic "
        f"WHERE market='E' AND name LIKE '%{kw}%' "
        "AND name NOT LIKE '%行业%' AND name NOT LIKE '%主题%' "
        "ORDER BY list_date LIMIT 40"
    )
    print(f"\n[OK] ETF name LIKE '%{kw}%':", "NONE" if m.empty else len(m))
    if not m.empty:
        print(m.to_string(index=False))
