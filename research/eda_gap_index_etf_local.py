# -*- coding: utf-8 -*-
"""缺口宽基指数的跟踪 ETF 盘点（本地 fund_basic + fund_daily）。

ETF 轮动视角：指数本身在 tushare 可取只是第一步，还必须有可交易的跟踪 ETF。
对不在 index_daily 本地覆盖里的梯度指数，这里查：
1. fund_basic 里有哪些 ETF 跟踪（按名称关键词）
2. 这些 ETF 的本地日线覆盖区间
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)

# (指数, 名称关键词) —— 关键词取 ETF 命名惯例；多关键词用 | 分隔
GAP = [
    ("000010.SH", "上证180"),
    ("000001.SH", "上证指数|上证综指"),
    ("399001.SZ", "深证成指"),
    ("399004.SZ", "深证100"),
    ("399673.SZ", "创业板50"),
    ("399102.SZ", "创业板综"),
    ("000698.SH", "科创100|科创板100"),
    ("000680.SH", "科创综|科创板综合"),
    ("399303.SZ", "国证2000"),
    ("899050.BJ", "北证50"),
    ("000510.SH", "A500"),
    ("930050.CSI", "A50__NOT_A500__"),
]

# 1) ETF 基础信息：market='E' 为 ETF
rows = []
for code, kw in GAP:
    pats = kw.split("|")
    like = " OR ".join([f"name LIKE '%{p}%'" for p in pats])
    if "NOT_A500" in kw:
        like = "(name LIKE '%中证A50%' AND name NOT LIKE '%A500%')"
    df = execute_sql(
        f"SELECT ts_code, name, fund_type, list_date, delist_date "
        f"FROM fund_basic WHERE market='E' AND ({like}) LIMIT 30"
    )
    df["index_code"] = code
    rows.append(df)
etf = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
print("[OK] matching ETFs in fund_basic:", len(etf))
if not etf.empty:
    print(etf[["index_code", "ts_code", "name", "fund_type", "list_date", "delist_date"]]
          .sort_values(["index_code", "name"]).to_string(index=False))

# 2) 这些 ETF 的本地 fund_daily 覆盖（只看在场/规模主流的若干只）
if not etf.empty:
    codes = "','".join(etf["ts_code"].tolist())
    cov = execute_sql(
        f"SELECT ts_code, COUNT(*) AS n_rows, MIN(trade_date) AS d_min, "
        f"MAX(trade_date) AS d_max FROM fund_daily WHERE ts_code IN ('{codes}') "
        f"GROUP BY ts_code ORDER BY d_min"
    )
    print("\n[OK] fund_daily coverage for matched ETFs:")
    print(cov.to_string(index=False))
