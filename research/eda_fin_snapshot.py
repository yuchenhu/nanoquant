# -*- coding: utf-8 -*-
"""panel_financial_statements_snapshot 全量 EDA（2026-10-05，回补完成后验收）。

四段：
  1. 覆盖概览：snapshot_date 列表、每月股票数/报告期数/行数、月度连续性
  2. 结构核验：PK 重复、每股报告份数分布、三表/市值覆盖率
  3. PE/PB 自算分布：半年频截面（6/12 月末），最新报告期口径
  4. 个股 case：茅台/工行/宁德时代在若干快照的 PE/PB，对照公开常识区间

口径注记（重要）：
  - 表内嵌 total_mv = actual_date 所在月末的市值（披露月市值，非快照月市值），
    本 EDA 的 PE/PB 分布用它可以做 sanity，正式指数 PE 聚合走月末 mv（SPEC 另有口径）
  - 三表金额列已 /10000（tushare 万元 -> 亿），panel_mv_monthly 的 mv 单位是万元
    -> PE/PB 计算前统一：利润/权益(亿) x 1e4 转万元
  - PE 为"最新报告期年化"粗口径（Q1x4 / H1x2 / 9Mx4/3 / FYx1），受季节性影响
    （银行 Q1 利润占比高会低估 PE），分布 sanity 够用，精确 TTM 留给聚合层
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.database import execute_sql

TBL = "panel_financial_statements_snapshot"
CASE_CODES = {
    "600519.SH": "贵州茅台",
    "601398.SH": "工商银行",
    "300750.SZ": "宁德时代",
}


def sec1_coverage():
    print("=" * 72)
    print("SEC 1  覆盖概览")
    print("=" * 72)
    d = execute_sql(f"""
        SELECT snapshot_date, COUNT(DISTINCT ts_code) n_stocks,
               COUNT(DISTINCT end_date) n_periods, COUNT(*) n_rows
        FROM {TBL} GROUP BY snapshot_date ORDER BY snapshot_date
    """)
    d["snapshot_date"] = pd.to_datetime(d["snapshot_date"])
    d["ym"] = d["snapshot_date"].dt.strftime("%Y-%m")
    months = pd.period_range(d["ym"].iloc[0], d["ym"].iloc[-1], freq="M")
    missing = sorted(set(months.astype(str)) - set(d["ym"]))
    print(f"快照数: {len(d)}  ({d['ym'].iloc[0]} -> {d['ym'].iloc[-1]})")
    print(f"月度连续性: {'[OK] 无缺月' if not missing else '[GAP] 缺 ' + str(missing)}")
    print(f"每月股票数: min={d.n_stocks.min()} 中位={int(d.n_stocks.median())} max={d.n_stocks.max()}")
    print(f"每月报告期数(distinct end_date): min={d.n_periods.min()} 中位={int(d.n_periods.median())} max={d.n_periods.max()}")
    print(f"每股报告份数: 中位 {d.n_rows.median()/d.n_stocks.median():.1f} 份（lookback 2 年理论 8 份）")
    print("首3月/末3月明细:")
    show = pd.concat([d.head(3), d.tail(3)])
    print(show[["ym", "n_stocks", "n_periods", "n_rows"]].to_string(index=False))
    return d


def sec2_structure(d):
    print()
    print("=" * 72)
    print("SEC 2  结构核验")
    print("=" * 72)
    dup = execute_sql(f"""
        SELECT COUNT(*) n FROM (
          SELECT snapshot_date, ts_code, end_date, COUNT(*) c
          FROM {TBL} GROUP BY snapshot_date, ts_code, end_date HAVING c > 1
        ) t
    """)
    print(f"PK(snapshot,ts_code,end_date) 重复组: {int(dup.iloc[0]['n'])}  "
          f"{'[OK]' if int(dup.iloc[0]['n']) == 0 else '[ERR]'}")

    # 抽 4 个年代截面看覆盖与报告份数
    snaps = ["2010-12-31", "2015-12-31", "2020-12-31", "2025-12-31"]
    rows = []
    for s in snaps:
        x = execute_sql(f"""
            SELECT ts_code,
                   MAX(has_bs) has_bs, MAX(has_inc) has_inc, MAX(has_cf) has_cf,
                   MAX(total_mv IS NOT NULL) has_mv, COUNT(*) n_rep
            FROM {TBL} WHERE snapshot_date = '{s}' GROUP BY ts_code
        """)
        rows.append({
            "snap": s[:7], "stocks": len(x),
            "has_bs%": f"{x.has_bs.mean()*100:.0f}", "has_inc%": f"{x.has_inc.mean()*100:.0f}",
            "has_cf%": f"{x.has_cf.mean()*100:.0f}", "has_mv%": f"{x.has_mv.mean()*100:.0f}",
            "rep=8%": f"{(x.n_rep == 8).mean()*100:.0f}",
            "rep_med": f"{x.n_rep.median():.0f}",
        })
    print(pd.DataFrame(rows).to_string(index=False))

    dv = execute_sql(f"""
        SELECT MIN(snapshot_date) d0 FROM {TBL}
        WHERE has_div = 1 AND cash_div IS NOT NULL
    """)
    print(f"分红列可用起点: {str(dv.iloc[0]['d0'])[:10]}（本地 dividend 源 2015-05 起，一致性待对照）")


def load_snap(snapshot: str) -> pd.DataFrame:
    """单快照全量：lean 列。"""
    return execute_sql(f"""
        SELECT ts_code, end_date, report_type, n_income_attr_p,
               total_hldr_eqy_exc_min_int, total_mv
        FROM {TBL} WHERE snapshot_date = '{snapshot}'
    """)


def pe_pb(x: pd.DataFrame) -> pd.DataFrame:
    """每股取最新 end_date 报告，算 PB / 年化 PE。返回每股一行。

    单位（茅台 2025-06 探针实证）：mv 与 利润/权益 列同为【万元】
    （tushare 源为元，快照 _process_columns 已 /10000 -> 万元），直接相除即可。
    """
    x = x.sort_values(["ts_code", "end_date"]).groupby("ts_code").tail(1).copy()
    ann = x["report_type"].map({1: 4.0, 2: 2.0, 3: 4.0 / 3.0, 4: 1.0})
    x["pb"] = x["total_mv"] / x["total_hldr_eqy_exc_min_int"]
    x["pe_ann"] = x["total_mv"] / (x["n_income_attr_p"] * ann)
    return x


def sec3_distribution():
    print()
    print("=" * 72)
    print("SEC 3  PE/PB 分布（半年频截面，最新报告期口径，含单位对齐说明）")
    print("=" * 72)
    snaps = execute_sql(f"""
        SELECT DISTINCT snapshot_date FROM {TBL}
        WHERE MONTH(snapshot_date) IN (6, 12) ORDER BY snapshot_date
    """)["snapshot_date"].tolist()
    snaps = [str(s)[:10] for s in snaps]
    out = []
    for s in snaps:
        x = pe_pb(load_snap(s))
        pb = x.loc[x["pb"] > 0, "pb"]
        pe = x.loc[x["pe_ann"] > 0, "pe_ann"]
        neg_eq = (x["total_hldr_eqy_exc_min_int"] <= 0).mean()
        neg_pr = (x["n_income_attr_p"] <= 0).mean()
        out.append({
            "ym": s[:7],
            "pb_med": round(pb.median(), 2), "pb_q25": round(pb.quantile(.25), 2),
            "pb_q75": round(pb.quantile(.75), 2),
            "pe_med": round(pe.median(), 1),
            "neg_eq%": round(neg_eq * 100, 1), "neg_pr%": round(neg_pr * 100, 1),
        })
    t = pd.DataFrame(out)
    print(t.to_string(index=False))
    return t


def sec4_cases(t):
    print()
    print("=" * 72)
    print("SEC 4  个股 case（对照公开常识区间）")
    print("=" * 72)
    case_snaps = ["2015-06-30", "2018-12-28", "2021-06-30", "2025-06-30"]
    case_snaps = [s for s in case_snaps
                  if s in set(str(x)[:10] for x in
                              execute_sql(f"SELECT DISTINCT snapshot_date FROM {TBL}")["snapshot_date"])]
    for code, name in CASE_CODES.items():
        for s in case_snaps:
            x = load_snap(s)
            row = pe_pb(x)
            r = row.loc[row["ts_code"] == code]
            if r.empty:
                print(f"{s[:7]} {name}({code}) [EMPTY] 无快照行")
                continue
            r = r.iloc[0]
            rep = "FY" if r["report_type"] == 4 else f"Q{int(r['report_type'])}"
            print(f"{s[:7]} {name}({code}) 报告={rep} "
                  f"mv={r['total_mv']/1e4:.0f}亿 pb={r['pb']:.2f} pe_ann={r['pe_ann']:.1f}")
    print()
    print("常识锚点（近似，用于方向核对）:")
    print("  工商银行 PB: 2010s ~1.0-1.3 -> 2018 ~0.8 -> 2024-25 ~0.55-0.70; PE ~4.5-7")
    print("  贵州茅台 PB: 2015 ~5-7, 2021 初峰值 ~13-16, 2024-25 ~7-9; PE(真TTM) 2021 峰 ~60-70")
    print("  全市场 PB 中位: 2015 峰 ~4+, 2018 底 ~1.7, 2024 初 ~1.6-1.8（历史低位区）")
    print("  注: pe_ann 为年化粗口径且 mv 取披露月，与真 TTM/月末 mv 有偏，方向与量级对即可")


if __name__ == "__main__":
    d = sec1_coverage()
    sec2_structure(d)
    t = sec3_distribution()
    sec4_cases(t)
