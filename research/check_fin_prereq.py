# -*- coding: utf-8 -*-
"""财务快照前置表覆盖体检（financial_statements_snapshot 补数前的检查）。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql

pd.set_option("display.width", 200)

# (表名, 业务日期列)
PREREQ = [
    ("disclosure_date", "end_date"),
    ("income", "end_date"),
    ("balancesheet", "end_date"),
    ("cashflow", "end_date"),
    ("dividend", "end_date"),
    ("panel_mv_monthly", "trade_date"),
]

rows = []
for t, col in PREREQ:
    try:
        d = execute_sql(f"SELECT COUNT(*) AS n, MIN({col}) AS d0, MAX({col}) AS d1 FROM {t}")
        rows.append([t, int(d.iloc[0]["n"]), str(d.iloc[0]["d0"]), str(d.iloc[0]["d1"]), "[OK]"])
    except Exception as e:
        rows.append([t, 0, "", "", f"[ERR] {str(e)[:60]}"])
rep = pd.DataFrame(rows, columns=["table", "rows", "min_date", "max_date", "status"])
print("[OK] financial snapshot prerequisites:")
print(rep.to_string(index=False))

snap = execute_sql(
    "SELECT COUNT(DISTINCT snapshot_date) AS n_snap, MIN(snapshot_date) AS d0, "
    "MAX(snapshot_date) AS d1 FROM panel_financial_statements_snapshot"
)
print("\n[OK] panel_financial_statements_snapshot current state:")
print(snap.to_string(index=False))
