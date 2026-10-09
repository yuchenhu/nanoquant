# -*- coding: utf-8 -*-
"""新入池 1 天 sync 验证的核验脚本（生成器 != 评估器：直接查库给证据）。

检查项：
1. ALL_INDEX_CODES（28 canonical + 3 alt）每只在 index_daily 的：总行数、起点、终点、
   验证窗（>=2026-09-25）行数 —— 验证窗有数 = 新码拉通
2. index_weight 新池代码的最新快照日期（若跑过）
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql
from config.universe import ALL_INDEX_CODES, INDEX_NAME, canonical

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 100)

q = "','".join(ALL_INDEX_CODES)
cov = execute_sql(
    f"SELECT ts_code, COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1, "
    f"SUM(CASE WHEN trade_date >= '2026-09-25' THEN 1 ELSE 0 END) AS n_check "
    f"FROM index_daily WHERE ts_code IN ('{q}') GROUP BY ts_code"
)
cov["name"] = [INDEX_NAME.get(canonical(c), canonical(c)) for c in cov["ts_code"]]
cov = cov.sort_values(["n_check", "d1"])
print("[OK] index_daily coverage for ALL_INDEX_CODES ({} codes):".format(len(ALL_INDEX_CODES)))
print(cov.to_string(index=False))

missing = [c for c in ALL_INDEX_CODES if c not in set(cov["ts_code"])]
stale = cov[cov["d1"].astype(str) < "2026-09-30"]
nocheck = cov[cov["n_check"] == 0]
print("\n[{}] not in index_daily at all: {}".format(
    "EMPTY" if missing else "OK", missing or "NONE"))
print("[{}] codes with d1 < 2026-09-30 (stale): {}".format(
    "WARN" if len(stale) else "OK", stale["ts_code"].tolist() or "NONE"))
print("[{}] codes with 0 rows in check window (>=2026-09-25): {}".format(
    "WARN" if len(nocheck) else "OK", nocheck["ts_code"].tolist() or "NONE"))

w = execute_sql(
    f"SELECT index_code, MAX(trade_date) AS d1, COUNT(DISTINCT trade_date) AS n_snap "
    f"FROM index_weight WHERE index_code IN ('{q}') GROUP BY index_code"
)
if not w.empty:
    w["name"] = [INDEX_NAME.get(canonical(c), c) for c in w["index_code"]]
    w = w.rename(columns={"index_code": "ts_code"}).sort_values("d1")
    print("\n[OK] index_weight latest snapshot per pool code:")
    print(w.to_string(index=False))
    w_missing = [c for c in ALL_INDEX_CODES if c not in set(w["ts_code"])]
    print("[{}] no weight at all: {}".format(
        "EMPTY" if w_missing else "OK", w_missing or "NONE"))
else:
    print("\n[EMPTY] index_weight has no pool codes (未跑 weight？)")
