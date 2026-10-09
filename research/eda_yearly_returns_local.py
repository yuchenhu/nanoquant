# -*- coding: utf-8 -*-
"""池内指数年度收益表（数据分布 vs A股现实交叉验证用）+ 北证50 ETF 宽关键词兜底。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)

# 1) 年度收益：年末收盘 / 上年末收盘 - 1
df = execute_sql(
    "SELECT ts_code, trade_date, close FROM index_daily "
    "WHERE ts_code IN ('000016.SH','000300.SH','000905.SH','000906.SH','000852.SH',"
    "'932000.CSI','000688.SH','399006.SZ','000985.CSI')"
)
df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values(["ts_code", "trade_date"])
df["year"] = df["trade_date"].dt.year

# 每年最后一个交易日收盘
year_end = df.groupby(["ts_code", "year"]).tail(1)[["ts_code", "year", "close"]]
first = df.groupby(["ts_code", "year"]).head(1)[["ts_code", "year", "close"]]
first = first.rename(columns={"close": "close_first"})

# 逐年收益 = 本年末 / 上年末；指数首年用首日->年末
piv = year_end.pivot(index="year", columns="ts_code", values="close")
first_close = df.groupby("ts_code").head(1).set_index("ts_code")["close"].to_dict()
first_year = df.groupby("ts_code").head(1).set_index("ts_code")["year"].to_dict()

ret = pd.DataFrame(index=piv.index)
for col in piv.columns:
    pe = piv[col]
    prev = pe.shift(1)
    # 指数首年：用首日收盘作基（科创50 2019 只有 1 根，跳过）
    if col in first_year:
        fy = first_year[col]
        if fy in pe.index and not pd.isna(pe.get(fy)):
            prev.loc[fy] = first_close.get(col)
    r = pe / prev - 1
    ret[col] = (r * 100).round(1)
print("[OK] calendar-year returns (%), 2010-2026:")
print(ret.to_string())

# 2) 2026-07-09 收盘快照（供外部来源同日对照）
snap = execute_sql(
    "SELECT ts_code, close FROM index_daily WHERE trade_date='2026-07-09' "
    "AND ts_code IN ('000016.SH','000300.SH','000905.SH','000852.SH','932000.CSI',"
    "'000688.SH','399006.SZ','000985.CSI') ORDER BY ts_code"
)
print("\n[OK] closes on 2026-07-09 (local tushare):")
print(snap.to_string(index=False))

# 3) 北证50 ETF 宽关键词兜底
for kw in ["北证", "北交所"]:
    m = execute_sql(
        f"SELECT ts_code, name, market, list_date, delist_date FROM fund_basic "
        f"WHERE name LIKE '%{kw}%' LIMIT 20"
    )
    print(f"\n[OK] fund_basic name LIKE '%{kw}%':", "NONE" if m.empty else len(m))
    if not m.empty:
        print(m.to_string(index=False))
