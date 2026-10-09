# -*- coding: utf-8 -*-
"""index 族数据完备性核验（回补前基线 / 回补后验收 通用）。

覆盖五层：
  1) index_basic        —— 31 池内码（含 alt）是否都有基础信息
  2) index_daily        —— canonical 码覆盖 vs 预期起点（EDA 实证值）+ 年度行数体检
  3) index_weight       —— 全部码（含 alt）月度快照深度（Tier B 因子/成分聚合的前提）
  4) index_member_all / index_classify —— 成分归属兜底数据源覆盖
  5) panel_index_membership_monthly —— 成分 panel 现状 vs 新池（是否需要重建）

标记一律 ASCII：[OK] / [WARN] / [ERR] / [EMPTY] / [GAP]
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql
from config.universe import ALL_INDEX_CODES, CANONICAL_INDEX_CODES, INDEX_NAME, canonical

pd.set_option("display.width", 240)
pd.set_option("display.max_rows", 80)

# 预期日线起点（2026-10-04 EDA 实证；回补后 d0 应 <= expected+7 天）
EXPECT_START = {
    "000016.SH": "2004-01-02", "000300.SH": "2005-01-04", "000905.SH": "2005-01-04",
    "000852.SH": "2005-01-04", "932000.CSI": "2013-12-31", "399303.SZ": "2010-01-04",
    "399006.SZ": "2010-06-01", "000688.SH": "2019-12-31", "000906.SH": "2005-01-04",
    "000985.CSI": "2004-12-31", "899050.BJ": "2022-11-21",
    "000922.CSI": "2004-12-31", "000015.SH": "2004-12-31", "399324.SZ": "2006-01-24",
    "H00922.CSI": "2004-12-31", "H30269.CSI": "2005-12-30", "930955.CSI": "2005-12-30",
    "930740.CSI": "2004-12-31", "932365.CSI": "2013-12-31", "932366.CSI": "2013-12-31",
    "932367.CSI": "2013-12-31", "980092.SZ": "2012-12-31", "000919.CSI": "2004-12-31",
    "000918.CSI": "2004-12-31", "932315.CSI": "2013-12-31", "932430.CSI": "2010-12-31",
    "000925.CSI": "2004-12-31", "932039.CSI": "2016-12-30",
}
WATERMARK = "2026-09-30"  # 节前最后交易日，回补后 d1 应 >= 此值

print("=" * 96)
print("[STEP 1] index_basic：池内 31 码（含 alt）")
q_all = "','".join(ALL_INDEX_CODES)
ib = execute_sql(f"SELECT ts_code FROM index_basic WHERE ts_code IN ('{q_all}')")
have = set(ib["ts_code"])
miss = [c for c in ALL_INDEX_CODES if c not in have]
print("[{}] basic 缺失: {}".format("EMPTY" if miss else "OK", miss or "NONE"))

print("=" * 96)
print("[STEP 2] index_daily：canonical {} 码覆盖 vs 预期起点（watermark={}）".format(
    len(CANONICAL_INDEX_CODES), WATERMARK))
q_can = "','".join(CANONICAL_INDEX_CODES)
daily = execute_sql(
    f"SELECT ts_code, trade_date, close FROM index_daily WHERE ts_code IN ('{q_can}')"
)
rows = []
for c in CANONICAL_INDEX_CODES:
    sub = daily[daily["ts_code"] == c]
    if sub.empty:
        rows.append([c, INDEX_NAME.get(c, c), 0, "", "", "N/A", "[EMPTY]"])
        continue
    td = pd.to_datetime(sub["trade_date"])
    d0, d1 = td.min(), td.max()
    yr = td.dt.year.value_counts()
    bad = [int(y) for y, n in yr.items()
           if y not in (d0.year, d1.year) and not (180 <= n <= 260)]
    exp = EXPECT_START.get(c, "")
    # 验收口径：d0 <= 预期+7 天 且 d1 >= 水位
    ok_start = (not exp) or (d0 <= pd.Timestamp(exp) + pd.Timedelta(days=7))
    ok_end = d1 >= pd.Timestamp(WATERMARK)
    status = "[OK]" if (ok_start and ok_end and not bad) else (
        "[GAP]" if not ok_start else ("[STALE]" if not ok_end else "[WARN]"))
    rows.append([c, INDEX_NAME.get(c, c), len(sub), str(d0.date()), str(d1.date()), exp,
                 status + (" gap:" + ",".join(map(str, bad)) if bad else "")])
rep = pd.DataFrame(rows, columns=["ts_code", "name", "rows", "d0", "d1", "expect_d0", "status"])
print(rep.to_string(index=False))
n_bad = (rep["status"] != "[OK]").sum()
print("\n[{}] daily 验收: {} OK / {} 待回补或异常（GAP=起点不足, STALE=未到水位）".format(
    "OK" if n_bad == 0 else "WARN", len(rep) - n_bad, n_bad))

print("=" * 96)
print("[STEP 3] index_weight：全部 {} 码（含 alt）月度快照深度".format(len(ALL_INDEX_CODES)))
w = execute_sql(
    f"SELECT index_code, COUNT(*) AS n, COUNT(DISTINCT trade_date) AS n_snap, "
    f"MIN(trade_date) AS d0, MAX(trade_date) AS d1 FROM index_weight "
    f"WHERE index_code IN ('{q_all}') GROUP BY index_code"
)
w["name"] = [INDEX_NAME.get(canonical(c), c) for c in w["index_code"]]
w = w.sort_values(["n_snap", "index_code"])
print(w.to_string(index=False))
no_w = [c for c in ALL_INDEX_CODES if c not in set(w["index_code"])]
shallow = w[pd.to_datetime(w["d0"]) > "2018-01-01"]["index_code"].tolist()
print("\n[WARN] 无 weight 的码（Tier B 因子/成分聚合不可用）: {}".format(no_w or "NONE"))
print("[WARN] weight 仅近年才有（历史浅）: {}".format(shallow or "NONE"))

print("=" * 96)
print("[STEP 4] index_member_all / index_classify 兜底源")
for tbl in ("index_member_all", "index_classify"):
    try:
        desc = execute_sql(f"DESCRIBE {tbl}")
        cols = desc["Field"].tolist()
        code_col = next((x for x in ("index_code", "ts_code") if x in cols), None)
        if code_col:
            m = execute_sql(
                f"SELECT {code_col} AS c, COUNT(*) AS n FROM {tbl} "
                f"WHERE {code_col} IN ('{q_all}') GROUP BY {code_col}"
            )
            hit = m["c"].tolist()
            print("[{}] {} ({}): 命中 {}/{} —— 缺: {}".format(
                "OK" if len(hit) == len(ALL_INDEX_CODES) else "WARN", tbl, code_col,
                len(hit), len(ALL_INDEX_CODES),
                [c for c in ALL_INDEX_CODES if c not in hit] or "NONE"))
        else:
            print("[WARN] {}: 无 index_code/ts_code 列，列为 {}".format(tbl, cols))
    except Exception as e:
        print("[ERR] {}: {}".format(tbl, str(e)[:60]))

print("=" * 96)
print("[STEP 5] panel_index_membership_monthly：现状 vs 新池")
mem = execute_sql(
    "SELECT index_code, COUNT(DISTINCT trade_date) AS n_months, COUNT(DISTINCT ts_code) AS n_stk, "
    "MIN(trade_date) AS d0, MAX(trade_date) AS d1 "
    "FROM panel_index_membership_monthly GROUP BY index_code"
)
mem["name"] = [INDEX_NAME.get(c, c) for c in mem["index_code"]]
mem = mem.sort_values("index_code")
print(mem.to_string(index=False))
new_need = [c for c in CANONICAL_INDEX_CODES if c not in set(mem["index_code"])]
old_left = [c for c in mem["index_code"].tolist() if c not in CANONICAL_INDEX_CODES]
print("\n[WARN] membership 面板缺的新池码（需重建）: {}".format(new_need or "NONE"))
print("[WARN] membership 面板残留的旧池码（931052=国信价值，重建后消失）: {}".format(old_left or "NONE"))
