# -*- coding: utf-8 -*-
"""smart beta / 全市场基座 / 行业线 数据可得性 EDA。

1) 候选风格指数 + 全A 基座候选：逐码拉 tushare index_daily 全历史，
   输出行数/首末日期/年度行数体检（一年 <180 或 >260 且非首末年 = gap）
2) 行业中性低波等代码从本地 index_basic 反查
3) 本地 sw_daily（申万）行业线覆盖盘点 —— 判断行业信号是否已够用
口径：fields 只取 close，减少传输；限频自动 sleep 重试。
标记一律 ASCII：[OK] / [WARN] / [ERR] / [EMPTY]
"""
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tushare as ts

from config.database import execute_sql
from config.settings import settings

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 100)

pro = ts.pro_api(settings.tushare_token)

CAND = [
    # ---- 全市场基座候选（回归/残差用）----
    ("000985.CSI", "中证全指[base]"),
    ("399317.SZ", "国证A指[base]"),
    # ---- 红利 ----
    ("000922.CSI", "中证红利"),
    ("000015.SH", "上证红利"),
    ("399324.SZ", "深证红利"),
    ("H00922.CSI", "中证红利全收益[TR]"),
    # ---- 红利低波 ----
    ("H30269.CSI", "中证红利低波动"),
    ("930955.CSI", "红利低波100"),
    ("930740.CSI", "300红利低波"),
    # ---- 自由现金流 ----
    ("932365.CSI", "中证现金流"),
    ("932366.CSI", "300现金流"),
    ("932367.CSI", "500现金流"),
    ("980092.SZ", "国证自由现金流"),
    # ---- 价值 / 成长 / 质量 / 基本面 / 股东回报 ----
    ("000919.CSI", "300价值"),
    ("000918.CSI", "300成长"),
    ("932430.CSI", "中证质量[2025新]"),
    ("932315.CSI", "中证红利质量"),
    ("000925.CSI", "基本面50"),
    ("932039.CSI", "央企股东回报"),
]


def pull_daily(code: str):
    """index_daily 全历史（限频自动退避重试）。"""
    for _ in range(3):
        try:
            return pro.index_daily(
                ts_code=code, start_date="19900101", end_date="20261004",
                fields="ts_code,trade_date,close",
            )
        except Exception as e:
            if ("频率" in str(e)) or ("超限" in str(e)):
                print("[WARN] rate limited on {}, sleep 62s".format(code))
                time.sleep(62)
                continue
            print("[ERR] {} api error: {}".format(code, e))
            return None
    return None


rows = []
for code, nm in CAND:
    df = pull_daily(code)
    if df is None or len(df) == 0:
        rows.append([code, nm, 0, "", "", "[EMPTY]"])
        continue
    td = pd.to_datetime(df["trade_date"])
    yr_counts = td.dt.year.value_counts().sort_index()
    y0, y1 = int(td.min().year), int(td.max().year)
    bad = [int(y) for y, n in yr_counts.items()
           if y not in (y0, y1) and not (180 <= n <= 260)]
    rows.append([
        code, nm, len(df), str(td.min().date()), str(td.max().date()),
        "gap:" + ",".join(map(str, bad)) if bad else "[OK]",
    ])

rep = pd.DataFrame(rows, columns=["ts_code", "name", "n_rows", "first", "last", "year_check"])
print("[OK] tushare index_daily availability ({} codes):".format(len(CAND)))
print(rep.to_string(index=False))

# ---- 行业中性低波代码反查（低波 smart beta 候选）----
lowvol = execute_sql(
    "SELECT ts_code, name, market, base_date, list_date FROM index_basic "
    "WHERE name LIKE '%行业中性低波%' OR name LIKE '%低波动%' "
    "AND market IN ('CSI','SSE') LIMIT 15"
)
print("\n[OK] index_basic low-vol lookup:")
print(lowvol.to_string(index=False) if not lowvol.empty else "[EMPTY]")

# ---- 本地 sw_daily（申万）行业线覆盖 ----
sw = execute_sql(
    "SELECT ts_code, COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1 "
    "FROM sw_daily GROUP BY ts_code ORDER BY ts_code"
)
l1 = sw[sw["ts_code"].str.startswith("801")].copy()
print("\n[OK] local sw_daily: total codes={}, L1(801xxx) codes={}".format(
    len(sw), len(l1)))
if not l1.empty:
    print("[OK] L1 coverage: rows {}-{}, first {}-{}, last {}-{}".format(
        l1["n"].min(), l1["n"].max(),
        l1["d0"].min(), l1["d0"].max(), l1["d1"].min(), l1["d1"].max()))
    print(l1.to_string(index=False))
