# -*- coding: utf-8 -*-
"""更新策略注册表盘点 + 数据缺口体检（纯查库，不触发任何 sync）。

输出四部分：
  1) etl 接入层 31 接口：策略/水位列/写模式 + 水位漂移 + 覆盖
  2) 加工层 17 任务：cadence/水位列 + 覆盖
  3) 起点对照：d0 vs 理论起点（项目回补口径 / 源端起点）
  4) 逐年断档扫描：d0 与 d1 之间每个完整年 distinct biz_date 计数，
     0 = 断档年，低于阈值 = 稀疏年（按策略分档阈值）
最后：minimalist 补数命令建议（只列异常项）

判定口径：
  daily   : d1 >= 2026-09-30（当前节前水位）
  monthly : d1 >= 2026-08-31（最近完整月末）
  period  : d1 >= 2026-06-30（最近已披露报告期）
  refresh : rows > 0
标记一律 ASCII：[OK] / [STALE] / [EMPTY] / [WARN] / [GAP]
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.database import execute_sql

pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 120)

DAILY_TARGET = "2026-09-30"
MONTHLY_TARGET = "2026-08-31"
PERIOD_TARGET = "2026-06-30"

# 理论起点（项目回补口径；源端受限的注明）。None = 清单类不适用。
ORIGIN = {
    "trade_cal": None, "stock_basic": None, "index_basic": None,
    "index_classify": None, "index_member_all": None, "fund_basic": None,
    "daily": "20100101", "adj_factor": "20100101", "daily_basic": "20100101",
    "moneyflow": "20100101", "suspend_d": "20100101", "sw_daily": "20100101",
    "fund_daily": "20100101", "fund_adj": "20100101", "fund_share": "20100101",
    "fund_factor_pro": "20100101", "fund_nav": "20100101",
    "index_weight": "20100101",
    "weekly": "20100108", "monthly": "20100129",
    "index_daily": "20041231", "index_dailybasic": "20041231",
    "income": "20100331", "balancesheet": "20100331", "cashflow": "20100331",
    "disclosure_date": "20100331", "margin": "20100331",
    "stock_st": "20160809",          # 源端起点
    "limit_list_d": "20191128",      # 源端起点（涨跌停清单 2019-11 起）
    "moneyflow_hsgt": "20141117",    # 沪港通开通
    "dividend": "20150513",          # 源端早年缺失（ex_date 口径）
}

# 逐年 distinct biz_date 的健康阈值（完整年）；低频表按表覆写
YEAR_MIN = {"by_trade_date": 180, "by_period": 4, "by_ex_date": 150,
            "daily": 180, "monthly": 10, "period": 4}
# 表本身低频的按表覆写（周线 ~52/年，月线/月度快照 12/年）：
# 误用 180 阈值会把所有年份误报 [THIN]（2026-10-04 首跑实测踩过）
YEAR_MIN_BY_API = {
    "weekly": 45, "monthly": 10, "index_weight": 10,
    "index_membership_monthly": 10, "market_sentiment_monthly": 10,
    "factor_valuation": 10, "fin_statement_panel": 10, "fin_indicator_snapshot": 10,
    "stock_mv_monthly": 10,
}


def table_state(table: str, biz_col: str):
    """(rows, d0, d1)；表不存在 (None,'','')。biz_col 空（full_refresh）只数行。"""
    try:
        if biz_col:
            d = execute_sql(
                f"SELECT COUNT(*) AS n, MIN({biz_col}) AS d0, MAX({biz_col}) AS d1 FROM {table}"
            )
        else:
            d = execute_sql(f"SELECT COUNT(*) AS n FROM {table}")
            d["d0"], d["d1"] = "", ""
        return int(d.iloc[0]["n"]), str(d.iloc[0]["d0"]), str(d.iloc[0]["d1"])
    except Exception:
        return None, "", ""


def year_gap_scan(table: str, biz_col: str, min_n: int):
    """d0..d1 之间完整年（不含首尾年）的 distinct biz 计数；返回 (断档年, 稀疏年)。"""
    try:
        d = execute_sql(
            f"SELECT YEAR({biz_col}) AS y, COUNT(DISTINCT {biz_col}) AS n "
            f"FROM {table} GROUP BY YEAR({biz_col}) ORDER BY y"
        )
    except Exception:
        return [], []
    if d.empty:
        return [], []
    ys = sorted(int(x) for x in d["y"] if x is not None)
    if len(ys) < 3:
        return [], []
    inner = range(ys[0] + 1, ys[-1])  # 完整年
    got = dict(zip((int(x) for x in d["y"]), (int(x) for x in d["n"])))
    gaps = [y for y in inner if got.get(y, 0) == 0]
    thin = [y for y in inner if 0 < got.get(y, 0) < min_n]
    return gaps, thin


def water(table: str) -> str:
    try:
        d = execute_sql(f"SELECT biz_date FROM etl_biz_date WHERE table_name='{table}'")
        return str(d.iloc[0]["biz_date"]) if not d.empty else ""
    except Exception:
        return ""


def judge(cadence: str, rows, d1: str) -> str:
    if rows is None or rows == 0:
        return "[EMPTY]"
    target = {"daily": DAILY_TARGET, "monthly": MONTHLY_TARGET, "period": PERIOD_TARGET}.get(cadence)
    if target is None:
        return "[OK]"
    return "[OK]" if d1 >= target else "[STALE]"


# ================= Part 1+3: etl 接入层（覆盖 + 起点对照 + 断档） =================
print("=" * 132)
print("[PART 1] etl 接入层（data.etl.loader.CALCULATORS）：覆盖 / 起点对照 / 断档")
from data.etl.base import (
    TushareByExDateCalculator,
    TushareByPeriodCalculator,
    TushareByTradeDateCalculator,
    TushareFullRefreshCalculator,
)
from data.etl.loader import CALCULATORS


def strat(cls) -> str:
    if issubclass(cls, TushareByPeriodCalculator):
        return "by_period"
    if issubclass(cls, TushareByExDateCalculator):
        return "by_ex_date"
    if issubclass(cls, TushareByTradeDateCalculator):
        return "by_trade_date"
    if issubclass(cls, TushareFullRefreshCalculator):
        return "full_refresh"
    return "unknown"


CAD = {"by_trade_date": "daily", "by_period": "period", "by_ex_date": "daily", "full_refresh": "refresh"}

rows = []
gap_report = []
for name, cls in CALCULATORS.items():
    s = strat(cls)
    cad = CAD.get(s, "refresh")
    tbl = cls.table_name
    n, d0, d1 = table_state(tbl, cls.biz_date_col)
    w = water(tbl)
    org = ORIGIN.get(name)
    # 起点对照：d0 晚于理论起点 7 天以上 = 起点未补齐
    org_flag = ""
    if org and d0 and d0.replace("-", "") > org and name != "":
        org_flag = f"[GAP start>{org[:4]}]" if int(d0[:4]) > int(org[:4]) + 1 else ""
    drift = "[WARN water!=max]" if (w and d1 and w.replace("-", "") < d1.replace("-", "")) else ""
    gaps, thin = ([], [])
    if n and cls.biz_date_col:
        min_n = YEAR_MIN_BY_API.get(name, YEAR_MIN.get(s, 1))
        gaps, thin = year_gap_scan(tbl, cls.biz_date_col, min_n)
    gap_flag = "[GAP " + ",".join(map(str, gaps)) + "]" if gaps else ""
    thin_flag = "[THIN " + ",".join(map(str, thin)) + "]" if thin else ""
    status = judge(cad, n, d1) + drift + org_flag + gap_flag + thin_flag
    if gaps or thin or org_flag:
        gap_report.append((name, "etl", org or "", d0, d1, gaps, thin))
    rows.append([name, tbl, s, n or 0, d0, d1, (org or "")[:10], w, status])

rep = pd.DataFrame(rows, columns=["api", "table", "strategy", "rows", "d0", "d1", "origin", "water", "status"])
print(rep.to_string(index=False))
print("\n[SUM] etl: {} OK / {} STALE / {} EMPTY / {} 有断档或稀疏".format(
    (rep.status.str.startswith("[OK]")).sum(),
    (rep.status.str.startswith("[STALE]")).sum(),
    (rep.status.str.startswith("[EMPTY]")).sum(),
    len(gap_report)))

# ================= Part 2: 加工层 =================
print("=" * 132)
print("[PART 2] 加工层（pipeline/schedule_compute.json）")
sched = json.loads((ROOT / "pipeline" / "schedule_compute.json").read_text(encoding="utf-8"))
tasks = [dict(t, _sec=sec) for sec in ("daily", "monthly") for t in sched.get(sec, [])]

rows = []
for t in tasks:
    cls_path = t["class"]
    try:
        mod, cls_name = cls_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod), cls_name)
        inst = cls()
        tbl, biz = inst.table_name, inst.biz_date_col
    except Exception as e:
        rows.append([t.get("task_id"), t["_sec"], "?", 0, "", "", "", f"[ERR] {str(e)[:40]}"])
        continue
    n, d0, d1 = table_state(tbl, biz)
    w = water(tbl)
    cad = "monthly" if t["_sec"] == "monthly" else "daily"
    gaps, thin = ([], [])
    if n and biz:
        min_n = YEAR_MIN_BY_API.get(t.get("task_id", ""), YEAR_MIN.get(cad, 1))
        gaps, thin = year_gap_scan(tbl, biz, min_n)
    gap_flag = "[GAP " + ",".join(map(str, gaps)) + "]" if gaps else ""
    thin_flag = "[THIN " + ",".join(map(str, thin)) + "]" if thin else ""
    rows.append([t.get("task_id"), t["_sec"], n or 0, d0, d1, w, biz, judge(cad, n, d1) + gap_flag + thin_flag])
    if gaps or thin:
        gap_report.append((t.get("task_id"), "compute", "", d0, d1, gaps, thin))

rep2 = pd.DataFrame(rows, columns=["task", "cadence", "rows", "d0", "d1", "water", "biz_col", "status"])
print(rep2.to_string(index=False))
print("\n[SUM] compute: {} OK / {} STALE / {} EMPTY".format(
    (rep2.status.str.startswith("[OK]")).sum(),
    (rep2.status.str.startswith("[STALE]")).sum(),
    (rep2.status.str.startswith("[EMPTY]")).sum()))

# ================= Part 3: 断档明细 =================
print("=" * 132)
print("[PART 3] 断档/稀疏明细（[GAP]=整年无数据, [THIN]=完整年低于阈值）")
if not gap_report:
    print("[OK] 无断档、无稀疏年")
for name, layer, org, d0, d1, gaps, thin in gap_report:
    print(f"  [{layer}] {name}: d0={d0} d1={d1} origin={org} GAP={gaps or '-'} THIN={thin or '-'}")

# ================= Part 4: minimalist 补数命令建议 =================
print("=" * 132)
print("[PART 4] minimalist 补数命令（只列异常项；断档年给区间命令）")
print("# --- etl ---")
for _, r in rep.iterrows():
    st = r["status"]
    if st.startswith("[OK]"):
        continue
    if st.startswith("[EMPTY]"):
        org = r["origin"]
        if org:
            print(f"# {r.api}: 空表 -> 从理论起点全量回补")
            print(f"scripts\\py.bat scripts\\ingest.py --only {r.api} --start {org} --end 20261004")
        else:
            print(f"scripts\\py.bat scripts\\ingest.py --only {r.api}")
    elif "GAP" in st:
        for name, layer, org, d0, d1, gaps, thin in gap_report:
            if name == r.api and gaps:
                lo, hi = min(gaps), max(gaps)
                print(f"# {r.api}: 断档年 {gaps} -> 按年回补（幂等）")
                print(f"scripts\\py.bat scripts\\ingest.py --only {r.api} --start {lo}0101 --end {hi}1231")
    elif st.startswith("[STALE]"):
        print(f"scripts\\py.bat scripts\\ingest.py --only {r.api}")  # 增量 catch-up
    elif st.startswith("[WARN]"):
        print(f"# {r.api}: 水位漂移，重跑一次增量即可")
        print(f"scripts\\py.bat scripts\\ingest.py --only {r.api}")
print("# --- 加工层 ---")
for _, r in rep2.iterrows():
    if r["status"].startswith("[OK]"):
        continue
    print(f"# {r.task}: {r['status']}")
    print(f"scripts\\py.bat scripts\\compute.py --solo {r.task}")
