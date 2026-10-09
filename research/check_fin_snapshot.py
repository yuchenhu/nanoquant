# -*- coding: utf-8 -*-
"""panel_financial_statements_snapshot 现状盘点（决策依据：快照节奏 / 深度 / 覆盖）。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 200)

t = execute_sql(
    "SELECT COUNT(*) AS rows_, COUNT(DISTINCT snapshot_date) AS n_snap, "
    "MIN(snapshot_date) AS d0, MAX(snapshot_date) AS d1 "
    "FROM panel_financial_statements_snapshot"
)
print("[OK] table overview:")
print(t.to_string(index=False))

s = execute_sql(
    "SELECT snapshot_date, COUNT(*) AS rows_, COUNT(DISTINCT ts_code) AS n_stk "
    "FROM panel_financial_statements_snapshot GROUP BY snapshot_date "
    "ORDER BY snapshot_date DESC LIMIT 10"
)
print("\n[OK] latest 10 snapshots:")
print(s.to_string(index=False) if not s.empty else "[EMPTY] no data")

if not s.empty:
    latest = str(s.iloc[0]["snapshot_date"])
    d = execute_sql(
        "SELECT AVG(n) AS avg_reports, MAX(n) AS max_reports, MIN(n) AS min_reports FROM "
        f"(SELECT COUNT(*) AS n FROM panel_financial_statements_snapshot "
        f"WHERE snapshot_date='{latest}' GROUP BY ts_code) x"
    )
    print(f"\n[OK] reports per stock at latest snapshot ({latest}):")
    print(d.to_string(index=False))
