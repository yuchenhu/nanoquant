# -*- coding: utf-8 -*-
"""宽基指数梯度 EDA -- 本地库覆盖盘点（一次性 research 脚本）。

问题：沪深+两创的宽基指数梯度（上证50 -> 微盘），本地 index_daily 各覆盖到什么区间？
输出：候选代码 x (行数, 起始日, 结束日) + 年度行数抽查 + 主键重复抽查。
标记一律 ASCII：[OK] / [WARN] / [ERR] / [EMPTY]
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根入 sys.path

from config.database import execute_sql

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 300)

# 候选梯度（大 -> 小 + 板块 + 全市场锚 + 微盘），代码后缀以 tushare 口径为准
CANDIDATES = [
    "000001.SH",  # 上证指数（锚）
    "000016.SH",  # 上证50
    "930050.CSI",  # 中证A50
    "000010.SH",  # 上证180
    "000300.SH",  # 沪深300
    "000510.CSI",  # 中证A500
    "399004.SZ",  # 深证100
    "000905.SH",  # 中证500
    "000906.SH",  # 中证800
    "000852.SH",  # 中证1000
    "932000.CSI",  # 中证2000
    "399303.SZ",  # 国证2000
    "000688.SH",  # 科创50
    "000698.SH",  # 科创100
    "399006.SZ",  # 创业板指
    "399673.SZ",  # 创业板50
    "899050.BJ",  # 北证50
    "399001.SZ",  # 深证成指（锚）
    "000985.CSI",  # 中证全指（锚）
    "8841431.WI",  # 万得微盘（预期缺失）
]

# 1) 相关表名
tabs = execute_sql("SHOW TABLES")
names = tabs.iloc[:, 0].tolist()
idx_tabs = [t for t in names if "index" in t]
print("[OK] index-related tables:", idx_tabs)

# 2) index_daily 覆盖盘点
q = "','".join(CANDIDATES)
cov = execute_sql(
    f"SELECT ts_code, COUNT(*) AS n_rows, MIN(trade_date) AS d_min, "
    f"MAX(trade_date) AS d_max FROM index_daily "
    f"WHERE ts_code IN ('{q}') GROUP BY ts_code ORDER BY d_min, ts_code"
)
print("\n[OK] index_daily coverage for candidates:")
print(cov.to_string(index=False))

missing = [c for c in CANDIDATES if c not in set(cov["ts_code"])]
print("\n[EMPTY] not in local index_daily:", missing)

# 3) 主键重复抽查（生成器 != 评估器：给证据）
dup = execute_sql(
    "SELECT ts_code, trade_date, COUNT(*) AS c FROM index_daily "
    "GROUP BY ts_code, trade_date HAVING c > 1 LIMIT 5"
)
print("\n[OK] dup (ts_code, trade_date) check:", "NONE" if dup.empty else dup.to_string(index=False))

# 4) 年度行数抽查：交易日/年 应在 ~237-250（A股现实性）
yr = execute_sql(
    "SELECT ts_code, YEAR(trade_date) AS y, COUNT(*) AS n FROM index_daily "
    "WHERE ts_code IN ('000300.SH','000852.SH','399006.SZ','000688.SH') "
    "GROUP BY ts_code, y ORDER BY ts_code, y"
)
print("\n[OK] rows per year (spot check 4 indices):")
print(yr.to_string(index=False))

# 5) 关键锚点收盘价（供外部来源交叉验证）
anchor = execute_sql(
    "SELECT ts_code, trade_date, close FROM index_daily "
    "WHERE (ts_code='000300.SH' AND trade_date IN ('20071016','20150608','20210218')) "
    "   OR (ts_code='399006.SZ' AND trade_date IN ('20150603','20210722')) "
    "   OR (ts_code='000688.SH' AND trade_date='20200804') "
    "ORDER BY ts_code, trade_date"
)
print("\n[OK] historical anchors:")
print(anchor.to_string(index=False))

# 6) 最新 5 个交易日收盘（供外部来源交叉验证）
latest = execute_sql(
    "SELECT ts_code, trade_date, close FROM index_daily "
    f"WHERE ts_code IN ('{q}') AND trade_date >= '2026-09-20' "
    "ORDER BY ts_code, trade_date"
)
print("\n[OK] latest closes (>=2026-09-20):")
print(latest.to_string(index=False))
