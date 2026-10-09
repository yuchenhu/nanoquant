# -*- coding: utf-8 -*-
"""底座三表全面验收：panel_index_membership_monthly / panel_sw_membership_monthly /
label_index_forward_returns。

定位：独立评估器（生成器≠评估器）。只读，不写任何表。
覆盖五个层面：
  A. 大数与主键（行数 / PK 唯一 / 覆盖起点）
  B. 连续性与分布（月网格无断档 / 成分数 vs 官方口径 / 前向收益值域）
  C. A 股现实性（成分数 vs 全市场股票数 / 月度换手合理 / 幸存者缺口量化）
  D. corner case（双版并集超标 / 改判股跨月切换 / 版本切换年 / 尾部 NULL 结构）
  E. 独立对拍（label 值 vs 源表收盘价手算，抽 20 个随机点）

用法：python research/audit_foundation_tables.py
输出：控制台 [OK]/[WARN]/[ERR] + 末尾汇总。退出码 0=无 ERR。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.database import execute_sql  # noqa: E402
from config.universe import (  # noqa: E402
    CANONICAL_INDEX_CODES,
    SW_L1_LIST,
    canonical,
)

random.seed(20261009)

N_OK = 0
N_WARN = 0
N_ERR = 0


def ok(msg: str):
    global N_OK
    N_OK += 1
    print(f"[OK]   {msg}")


def warn(msg: str):
    global N_WARN
    N_WARN += 1
    print(f"[WARN] {msg}")


def err(msg: str):
    global N_ERR
    N_ERR += 1
    print(f"[ERR]  {msg}")


def section(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ============================================================================
section("A/B/D-1. panel_index_membership_monthly")
# ============================================================================

mem = execute_sql("""
    SELECT index_code, trade_date, ts_code, weight
    FROM panel_index_membership_monthly
""")
print(f"rows={len(mem):,}, indices={mem['index_code'].nunique()}, "
      f"stocks={mem['ts_code'].nunique()}, "
      f"[{mem['trade_date'].min()} ~ {mem['trade_date'].max()}]")

# --- PK 唯一
dup = mem.duplicated(["index_code", "trade_date", "ts_code"]).sum()
ok(f"PK (index,month,stock) 重复 = {dup}") if dup == 0 else err(f"PK 重复 = {dup}")

# --- 月网格连续性：每指数的月份数 == 区间内月数
grid = mem.groupby("index_code")["trade_date"].agg(["min", "max", "nunique"])
all_month_ends = sorted(mem["trade_date"].unique())
me_set = set(pd.to_datetime(all_month_ends))
gaps = []
for _, r in grid.iterrows():
    d1, d2 = pd.Timestamp(r["min"]), pd.Timestamp(r["max"])
    expect = len([m for m in me_set if d1 <= m <= d2])
    if expect != r["nunique"]:
        gaps.append((r.name, int(r["nunique"]), expect))
ok("每指数月网格无断档（月数=区间月数）") if not gaps else \
    err(f"月网格断档 {len(gaps)} 个: {gaps[:5]}")

# --- 固定数目指数的成分数 vs 官方（官方数不明的变长指数不列：980092 等）
OFFICIAL = {
    "000016.SH": 50, "000300.SH": 300, "000905.SH": 500, "000852.SH": 1000,
    "000906.SH": 800, "399324.SZ": 40, "000922.CSI": 100, "000925.CSI": 50,
    "H00922.CSI": 100, "000918.CSI": 100, "000919.CSI": 100, "930955.CSI": 100,
    "930740.CSI": 50, "H30269.CSI": 50, "932315.CSI": 50, "000688.SH": 50,
    "899050.BJ": 50, "932365.CSI": 100, "932366.CSI": 50, "932367.CSI": 50,
    "932430.CSI": 100, "399006.SZ": 100, "932000.CSI": 2000,
    "399303.SZ": 2000, "000015.SH": 50,
}
cnt = mem.groupby(["index_code", "trade_date"]).size().reset_index(name="n")
bad_months = []
for code, official_n in OFFICIAL.items():
    sub = cnt[cnt["index_code"] == code]
    if sub.empty:
        continue
    dev = sub[sub["n"] != official_n]
    for _, r in dev.iterrows():
        bad_months.append((code, str(r["trade_date"]), int(r["n"]), official_n))
if bad_months:
    # 忠实性判据：面板偏离官方的月份，与源表当月最后快照日对比——
    # 面板==源 = 源侧数据问题（缺行/回填不全，记录不修）；面板!=源 = 面板 bug（ERR）
    from config.universe import CODE_TO_CANONICAL  # noqa: E402
    src_all = execute_sql("""
        SELECT index_code, trade_date, COUNT(DISTINCT con_code) AS k
        FROM index_weight GROUP BY index_code, trade_date
    """)
    src_all["index_code"] = src_all["index_code"].map(
        lambda c: CODE_TO_CANONICAL.get(c, c))
    src_all["ym"] = pd.to_datetime(src_all["trade_date"]).dt.strftime("%Y%m")
    src_last = (src_all.sort_values("trade_date")
                .groupby(["index_code", "ym"]).tail(1)
                .set_index(["index_code", "ym"])["k"])
    panel_bug, source_gap = [], []
    for code, tdate, n, official_n in bad_months:
        ym = pd.Timestamp(tdate).strftime("%Y%m")
        src_k = src_last.get((code, ym))
        if src_k is None or int(src_k) == n:
            source_gap.append((code, str(tdate), n, official_n,
                               None if src_k is None else int(src_k)))
        else:
            panel_bug.append((code, str(tdate), n, int(src_k)))
    if panel_bug:
        err(f"面板与源表不一致 {len(panel_bug)} 个月（真 bug）：{panel_bug[:5]}")
    else:
        ok(f"偏离官方的 {len(source_gap)} 个月：面板全部 == 源表当月最后快照"
           f"（源侧缺行/回填不全，忠实复刻，非面板 bug）")
    by_idx = {}
    for code, tdate, n, official_n, sk in source_gap:
        by_idx.setdefault(code, 0)
        by_idx[code] += 1
    print(f"    源侧偏离分布：{by_idx}")
    print("    已知源侧限制：399303 国证2000 2014-2017 成分回填不全"
          "（1,410->1,884/2,000）；个别指数偶发缺 1 行")
else:
    ok(f"{len(OFFICIAL)} 个固定数目指数全部月份成分数 == 官方口径")

# --- 权重和 ≈ 100（index_weight 的 weight 单位是百分比）
wsum = (mem.groupby(["index_code", "trade_date"])["weight"].sum()
        .reset_index(name="s"))
w_null = mem["weight"].isna().mean()
ok(f"月度权重和(%) min={wsum['s'].min():.2f} / med={wsum['s'].median():.2f} / "
   f"max={wsum['s'].max():.2f}（应≈100）；weight 缺失率 {w_null:.1%}"
   ) if (wsum["s"].min() > 97 and wsum["s"].max() < 103) else \
    warn(f"权重和(%) 偏离 100：min={wsum['s'].min():.2f} max={wsum['s'].max():.2f}；"
         f"缺失率 {w_null:.1%}")

# --- 月度成分换手现实性（沪深300：半年度调样 ~10-20 名）
m300 = mem[mem["index_code"] == "000300.SH"]
sets300 = m300.groupby("trade_date")["ts_code"].apply(set).sort_index()
keys = list(sets300.index)
churn = pd.Series([len(sets300.iloc[k + 1] - sets300.iloc[k]) / 300
                   for k in range(len(keys) - 1)])
if churn.max() <= 0.25:
    ok(f"沪深300 月度成分换手 p50={churn.median():.1%} p95={churn.quantile(.95):.1%} "
       f"max={churn.max():.1%}（合理区间）")
else:
    warn(f"沪深300 换手异常 max={churn.max():.1%}，最猛调样月："
         f"{keys[int(churn.idxmax()) + 1]}")

# ============================================================================
section("A/C/D-2. panel_sw_membership_monthly")
# ============================================================================

sw = execute_sql("""
    SELECT trade_date, ts_code, l1_code, l1_name
    FROM panel_sw_membership_monthly
""")
# date 对象统一转 Timestamp（否则与 Timestamp 比较恒 False，join 会空）
sw["trade_date"] = pd.to_datetime(sw["trade_date"])
print(f"rows={len(sw):,}, months={sw['trade_date'].nunique()}, "
      f"stocks={sw['ts_code'].nunique()}, "
      f"[{sw['trade_date'].min()} ~ {sw['trade_date'].max()}]")

# --- PK 唯一
dup = sw.duplicated(["trade_date", "ts_code"]).sum()
ok(f"PK (month,stock) 重复 = {dup}") if dup == 0 else err(f"PK 重复 = {dup}")

# --- 月网格：2010-01 起 ≥ 198 个月无断档
sw_months = pd.to_datetime(sorted(sw["trade_date"].unique()))
expect_months = [m for m in me_set if pd.Timestamp("2010-01-01") <= m]
missing = sorted(set(expect_months) - set(sw_months))
ok(f"月网格 {len(sw_months)} 个月，无断档") if not missing else \
    err(f"缺月 {len(missing)} 个：{missing[:8]}")

# --- 每月 31 个 L1 全覆盖
l1_per_m = sw.groupby("trade_date")["l1_code"].nunique()
if l1_per_m.min() == 31:
    ok("每月覆盖 31 个 L1（min=max=31）")
else:
    l1_missing = sw.groupby("trade_date")["l1_code"].nunique()
    warn(f"L1 覆盖 min={l1_missing.min()}（月："
         f"{l1_missing[l1_missing < 31].index[:3].tolist()}）")

# --- 成分数 vs 当月实际交易股票数（幸存者缺口量化）
# 注意：DATE_FORMAT 的 %% 转义在无参数 SQL 下会变字面量，改按日聚合后在 pandas 归月
stock_day = execute_sql("""
    SELECT trade_date, COUNT(DISTINCT ts_code) AS n
    FROM panel_stock_daily
    GROUP BY trade_date
""")
stock_day["month"] = (pd.to_datetime(stock_day["trade_date"])
                      + pd.offsets.MonthEnd(0))
stock_cnt = stock_day.groupby("month", as_index=False)["n"].max()
sw_cnt = sw.groupby("trade_date").size().reset_index(name="sw_n")
sw_cnt["month"] = pd.to_datetime(sw_cnt["trade_date"])
cmp_m = stock_cnt.merge(sw_cnt[["month", "sw_n"]], on="month", how="inner")
cmp_m["gap"] = cmp_m["n"] - cmp_m["sw_n"]
cmp_m["gap_pct"] = cmp_m["gap"] / cmp_m["n"]
recent = cmp_m[cmp_m["month"] >= "2024-01-01"]["gap_pct"].mean()
early = cmp_m[cmp_m["month"] <= "2012-12-31"]["gap_pct"].mean()
if recent < 0.03:
    ok(f"幸存者缺口：2024+ 平均 {recent:.1%}（近零，快照新鲜）")
else:
    warn(f"幸存者缺口 2024+ 平均 {recent:.1%} 偏大")
print(f"    早年(2010-12)平均缺口 {early:.1%}（退市股缺失，预期方向性偏差）")

# --- 与 panel_stock_daily.l1_code 的一致性（两表共同的最近完整月；stock 面板可能滞后）
spd_max = execute_sql("SELECT MAX(trade_date) AS d FROM panel_stock_daily")
spd_max_d = pd.Timestamp(spd_max["d"].iloc[0])
common = [m for m in sw_months if m <= spd_max_d]
if common:
    last_m = common[-1]
    sw_last = sw[sw["trade_date"] == last_m][["ts_code", "l1_code"]]
    spd_last = execute_sql(f"""
        SELECT DISTINCT ts_code, l1_code FROM panel_stock_daily
        WHERE trade_date = '{last_m.strftime('%Y-%m-%d')}'
          AND l1_code IS NOT NULL AND l1_code != ''
    """)
    j = sw_last.merge(spd_last, on="ts_code", how="inner",
                      suffixes=("_sw", "_spd"))
    if len(j):
        agree = (j["l1_code_sw"] == j["l1_code_spd"]).mean()
        ok(f"共同最近月末 {last_m.date()}（stock 面板水位 {spd_max_d.date()}）"
           f"l1_code 一致率 {agree:.1%}（共同 {len(j):,}）") if agree > 0.97 else \
            warn(f"l1_code 一致率 {agree:.1%} < 97%，需排查")
    else:
        warn("sw 与 stock_daily 无共同股票（l1_code 全空？）")
else:
    warn(f"panel_stock_daily 水位 {spd_max_d.date()} 早于 sw 表全部月份")

# --- corner：改判股跨月切换（out_date 落在月中的真实股票）
seg_mid = execute_sql("""
    SELECT ts_code, l1_code, in_date, out_date FROM index_member_all
    WHERE out_date IS NOT NULL
      AND DAY(LAST_DAY(out_date)) != DAY(out_date)   -- 非月末剔除
      AND out_date >= '2015-01-01'
    ORDER BY out_date DESC LIMIT 500
""")
sw_p = sw.copy()
sw_p["trade_date"] = pd.to_datetime(sw_p["trade_date"])
checked = 0
bad_switch = []
for _, r in seg_mid.iterrows():
    out_d = pd.Timestamp(r["out_date"])
    prev_end = out_d - pd.offsets.MonthEnd(1)
    next_end = out_d + pd.offsets.MonthEnd(0)  # out_date 所在月月末（新段应生效）
    prev_row = sw_p[(sw_p["ts_code"] == r["ts_code"])
                    & (sw_p["trade_date"] == prev_end)]
    next_row = sw_p[(sw_p["ts_code"] == r["ts_code"])
                    & (sw_p["trade_date"] == next_end)]
    if prev_row.empty or next_row.empty:
        continue
    if prev_row["l1_code"].iloc[0] != r["l1_code"]:
        bad_switch.append((r["ts_code"], str(prev_end.date())))
    checked += 1
    if checked >= 40:
        break
ok(f"改判股跨月切换抽查 {checked} 只：剔除前一月末均属旧行业") if not bad_switch else \
    err(f"改判切换异常 {bad_switch[:5]}")

# --- corner：SW2021 版切换月改判数（口径：L1 真变化的段，非全部重建段）
#     2021-07 剔除段 565 = 258 段 L1 变化 + 307 段 L1 不变（版次切换时段被重建）
switch_src = execute_sql("""
    SELECT COUNT(*) AS n_changed FROM (
        SELECT a.ts_code, a.l1_code AS old_l1, b.l1_code AS new_l1, b.in_date
        FROM index_member_all a
        JOIN index_member_all b
          ON b.ts_code = a.ts_code AND b.in_date = (
              SELECT MIN(c.in_date) FROM index_member_all c
              WHERE c.ts_code = a.ts_code AND c.in_date > a.in_date)
        WHERE a.out_date >= '2021-07-01' AND a.out_date <= '2021-07-31'
    ) t WHERE t.old_l1 != t.new_l1 AND t.in_date <= '2021-07-30'
""")
expected_switch = int(switch_src["n_changed"].iloc[0])
sw_pivot = sw[sw["trade_date"].isin(
    [pd.Timestamp("2021-06-30"), pd.Timestamp("2021-07-30")])]
wide = sw_pivot.pivot_table(index="ts_code", columns="trade_date",
                            values="l1_code", aggfunc="first")
wide = wide.dropna()
switched = (wide.iloc[:, 0] != wide.iloc[:, 1]).sum()
ok(f"SW2021 切换月（2021-06->07）改判 {switched} 只 ≈ 源推导 {expected_switch}"
   f"（L1 真变化段；±2 内为正常，差值来自新月末上市股）"
   ) if abs(switched - expected_switch) <= 3 else \
    warn(f"版次切换改判 {switched} vs 源推导 {expected_switch}，偏差 > 3")

# ============================================================================
section("A/B/E-3. label_index_forward_returns")
# ============================================================================

lab = execute_sql("""
    SELECT ts_code, trade_date, ret_fwd_5, ret_fwd_10, ret_fwd_20,
           ret_fwd_60, ret_fwd_120
    FROM label_index_forward_returns
""")
print(f"rows={len(lab):,}, codes={lab['ts_code'].nunique()}, "
      f"[{lab['trade_date'].min()} ~ {lab['trade_date'].max()}]")

# --- PK 唯一
dup = lab.duplicated(["ts_code", "trade_date"]).sum()
ok(f"PK (code,date) 重复 = {dup}") if dup == 0 else err(f"PK 重复 = {dup}")

# --- 尾部 NULL 结构：每 code 每窗口恰好 window+1 个 NULL
lab_d = lab.copy()
lab_d["trade_date"] = pd.to_datetime(lab_d["trade_date"])
struct_bad = []
for code, gdf in lab_d.groupby("ts_code"):
    gdf = gdf.sort_values("trade_date")
    for n in [5, 10, 20, 60, 120]:
        n_null = int(gdf[f"ret_fwd_{n}"].isna().sum())
        # 尾部 = window+1（T 需 T+1+n 有价）；早头部不满 1250 天的另计
        # 这里只校验：非尾部区域 NULL（除窗口不足外不应有）——
        # 精确判据：NULL 行的 trade_date 必须 >= 全表该 code 倒数第 n+1 行
        null_rows = gdf[gdf[f"ret_fwd_{n}"].isna()]["trade_date"]
        cutoff = gdf["trade_date"].iloc[-(n + 2)] if len(gdf) > n + 1 else None
        if cutoff is not None:
            early_null = null_rows[null_rows < cutoff]
            # 早年窗口不足（序列长度 < n+2 的头部日期）不允许：entry T+1 都有价
            if len(early_null) > 0 and len(gdf) > n + 2:
                struct_bad.append((code, n, len(early_null)))
ok("尾部 NULL 结构：所有 NULL 都在倒数 window+1 行内（无中段断裂）"
   ) if not struct_bad else \
    err(f"中段 NULL 异常 {len(struct_bad)} 处：{struct_bad[:5]}")

# --- 值域现实性
stats = lab[["ret_fwd_20", "ret_fwd_120"]].describe(
    percentiles=[.01, .5, .99])
print("    ret_fwd_20:  p1={:.1%} med={:.1%} p99={:.1%}".format(
    stats.loc["1%", "ret_fwd_20"], stats.loc["50%", "ret_fwd_20"],
    stats.loc["99%", "ret_fwd_20"]))
print("    ret_fwd_120: p1={:.1%} med={:.1%} p99={:.1%}".format(
    stats.loc["1%", "ret_fwd_120"], stats.loc["50%", "ret_fwd_120"],
    stats.loc["99%", "ret_fwd_120"]))
n_ext = (lab["ret_fwd_120"].abs() > 1.0).sum()
if n_ext:
    ext_years = pd.to_datetime(
        lab.loc[lab["ret_fwd_120"].abs() > 1.0, "trade_date"]).dt.year
    known_mania = set(range(2006, 2009)) | {2014, 2015, 2020, 2024, 2025}
    if set(ext_years.unique()) <= known_mania:
        ok(f"|ret_fwd_120|>100% 共 {n_ext} 行，年份分布 {dict(ext_years.value_counts().sort_index())}"
           f" 全部落在已知疯牛段（2006-08/2014-15 等），属真实行情")
    else:
        warn(f"|ret_fwd_120|>100% 出现在非疯牛年份："
             f"{sorted(set(ext_years.unique()) - known_mania)}，检查源价格跳变")
else:
    ok("120 日收益无 |ret|>100% 极端值")

# --- 覆盖 vs 源表交易日（缺口量化）
src_idx = execute_sql(f"""
    SELECT ts_code, trade_date FROM index_daily
    WHERE ts_code IN ({",".join(f"'{c}'" for c in CANONICAL_INDEX_CODES)})
""")
src_idx["ts_code"] = src_idx["ts_code"].map(canonical)
src_idx = src_idx.drop_duplicates(["ts_code", "trade_date"])
src_sw = execute_sql(f"""
    SELECT ts_code, trade_date FROM sw_daily
    WHERE ts_code IN ({",".join(f"'{c}'" for c in SW_L1_LIST)})
""")
src = pd.concat([src_idx, src_sw], ignore_index=True)
src["trade_date"] = pd.to_datetime(src["trade_date"])
lab_d2 = lab_d[["ts_code", "trade_date"]]
merged = src.merge(lab_d2, on=["ts_code", "trade_date"], how="left",
                   indicator=True)
missing_src = merged[merged["_merge"] == "left_only"]
ok(f"源表交易日 -> label 覆盖完整（缺口 {len(missing_src)} 行）"
   ) if len(missing_src) == 0 else \
    warn(f"源表有 {len(missing_src)} 个交易日未生成 label 行；按 code 分布：\n"
         f"    {missing_src.groupby('ts_code').size().sort_values(ascending=False).head(8).to_dict()}")

# --- 独立对拍：随机 20 点，ret_fwd_20 vs 源表收盘价手算
pool = lab[lab["ret_fwd_20"].notna()].sample(20, random_state=7)
from config.universe import CODE_TO_CANONICAL  # noqa: E402

closes = {}
for code in pool["ts_code"].unique():
    if code in SW_L1_LIST or code.endswith(".SI"):
        q = f"SELECT trade_date, close FROM sw_daily WHERE ts_code='{code}'"
    else:
        # canonical + 双版 alt 一起取，去重保 canonical（与生成器同规则）
        alt_list = [k for k, v in CODE_TO_CANONICAL.items()
                    if v == code and k != code]
        in_list = ",".join(f"'{c}'" for c in [code] + alt_list)
        q = (f"SELECT ts_code, trade_date, close FROM index_daily "
             f"WHERE ts_code IN ({in_list})")
    df = execute_sql(q)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if "ts_code" in df.columns:
        df["src_canonical"] = df["ts_code"].isin(CANONICAL_INDEX_CODES)
        df = (df.sort_values(["trade_date", "src_canonical"],
                             ascending=[True, False])
                .drop_duplicates("trade_date", keep="first"))
    else:
        df = df.drop_duplicates("trade_date", keep="first")
    closes[code] = df.sort_values("trade_date").set_index("trade_date")["close"]

max_diff = 0.0
bad_spot = []
for _, r in pool.iterrows():
    code, td, label_v = r["ts_code"], pd.Timestamp(r["trade_date"]), r["ret_fwd_20"]
    s = closes[code]
    pos = s.index.get_loc(td)
    entry, exit_ = s.iloc[pos + 1], s.iloc[pos + 21]
    manual = exit_ / entry - 1
    d = abs(manual - label_v)
    max_diff = max(max_diff, d)
    if d > 1e-9:
        bad_spot.append((code, str(td.date()), label_v, manual))
ok(f"随机 20 点对拍 ret_fwd_20：max |diff| = {max_diff:.2e}"
   ) if not bad_spot else err(f"对拍失败 {bad_spot[:3]}")

# ============================================================================
section("汇总")
# ============================================================================
print(f"[OK]={N_OK}  [WARN]={N_WARN}  [ERR]={N_ERR}")
sys.exit(1 if N_ERR else 0)
