# -*- coding: utf-8 -*-
"""纯 size 宽基池月频 EDA 报告。

口径：
- 数据：本地 index_daily（日线，水位 2026-07-09，未 sync）；
  本地缺失的池内指数（新入池未回补）用 tushare index_monthly 月线补齐月频面板
- 频率：月末最后交易日收盘价；分析统一截到 2026-06（2026-07 为残月：只有 7/9 前数据）
- 池：config/universe.py INDEX_POOL['size'] 中 role='rotation' 的 8 只
输出：覆盖 / 近 6 月月末收盘 / 年度收益 / 全期统计 / 全样本 + 最新 60M 相关 /
      高相关冗余对 / 两张图（research/figures/）
标记一律 ASCII：[OK] / [WARN] / [ERR] / [EMPTY]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import execute_sql
from config.settings import settings
from config.universe import INDEX_POOL

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)

ANALYSIS_END = pd.Period("2026-06", freq="M")  # 数据水位 2026-07-09，7 月残月，截到 6 月末

# ---- 1) 池与数据 ----
pool = {c: m for c, m in INDEX_POOL["size"].items() if m.get("role") == "rotation"}
codes = list(pool.keys())
names = {c: pool[c]["name"] for c in codes}
print("[OK] rotation pool ({}):".format(len(codes)))
for c in codes:
    print("     {}  {}".format(c, names[c]))

q = "','".join(codes)
df = execute_sql(
    "SELECT ts_code, trade_date, close FROM index_daily WHERE ts_code IN ('{}')".format(q)
)
df["trade_date"] = pd.to_datetime(df["trade_date"])
print("\n[OK] local index_daily: rows={}, {} -> {}".format(
    len(df), df["trade_date"].min().date(), df["trade_date"].max().date()))

px = df.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
try:
    mpx = px.resample("ME").last()  # pandas >= 2.2
except ValueError:
    mpx = px.resample("M").last()   # pandas < 2.2
mpx.index = mpx.index.to_period("M")

# ---- 2) 本地缺失的池内指数：tushare 月线补齐（月频面板够用）----
missing = [c for c in codes if c not in mpx.columns]
if missing:
    print("[WARN] not in local index_daily (新入池未 sync):", missing)
    try:
        import tushare as ts

        pro = ts.pro_api(settings.tushare_token)
        for c in missing:
            md = pro.index_monthly(
                ts_code=c, start_date="20100101", end_date="20260630",
                fields="ts_code,trade_date,close",
            )
            if md is None or len(md) == 0:
                print("[ERR] tushare index_monthly empty for", c)
                continue
            s = pd.Series(
                md["close"].astype(float).values,
                index=pd.to_datetime(md["trade_date"]).dt.to_period("M"),
            ).sort_index()
            mpx[c] = s
            print("[OK] fetched {} monthly bars from tushare: {} rows, {} -> {}".format(
                c, len(s), s.index[0], s.index[-1]))
    except Exception as e:
        print("[ERR] tushare fetch failed:", e)

mpx = mpx.loc[:ANALYSIS_END]  # 截到全月末
mret = mpx.pct_change()

# ---- 3) 月频覆盖 ----
first_m = mpx.apply(lambda s: s.first_valid_index())
cov = pd.DataFrame({
    "name": [names.get(c, c) for c in mpx.columns],
    "n_months": mpx.notna().sum().values,
    "first": [str(d) if d is not None else "" for d in first_m],
    "last": [str(mpx.index[-1])] * len(mpx.columns),
}, index=mpx.columns)
print("\n[OK] monthly coverage (full months only, ends {}):".format(ANALYSIS_END))
print(cov.to_string())

# ---- 4) 近 6 个月月末收盘 ----
print("\n[OK] last 6 month-end closes:")
print(mpx.tail(6).rename(columns=names).round(1).to_string())

# ---- 5) 年度收益（%）----
ye = mpx.groupby(mpx.index.year).last()
ye_ret = ye.pct_change() * 100
for c in mpx.columns:  # 各指数首年以首个有数月收盘为基（科创50 2019 只有基点一根）
    d0 = first_m[c]
    if d0 is not None and d0.year in ye.index:
        ye_ret.loc[d0.year, c] = (ye.loc[d0.year, c] / mpx.loc[d0, c] - 1) * 100
print("\n[OK] calendar-year returns % (2026 = 1-6 月 YTD):")
print(ye_ret.round(1).rename(columns=names).to_string())

# ---- 6) 全期统计 ----
rows = []
for c in mpx.columns:
    r = mret[c].dropna()
    nav = (1 + r).cumprod()
    years = len(r) / 12
    ann = nav.iloc[-1] ** (1 / years) - 1
    vol = r.std() * np.sqrt(12)
    dd = (nav / nav.cummax() - 1).min()
    rows.append([names.get(c, c), str(first_m[c]), round(ann * 100, 1), round(vol * 100, 1),
                 round(ann / vol, 2), round(dd * 100, 1), round((r > 0).mean() * 100, 0)])
stats = pd.DataFrame(rows, columns=["name", "since", "ann_ret%", "ann_vol%", "sharpe", "maxDD%", "win%mo"])
print("\n[OK] full-period stats (monthly, rf=0):")
print(stats.to_string(index=False))

# ---- 7) 相关矩阵：全样本 + 最新 60M 滚动 ----
cols = list(mpx.columns)
corr_full = mret.corr()
print("\n[OK] full-sample corr of monthly returns (pairwise):")
print(corr_full.rename(index=names, columns=names).round(2).to_string())

roll = mret.rolling(60, min_periods=48).corr()
corr_60 = roll.loc[roll.index.get_level_values(0)[-1]]
print("\n[OK] latest 60M rolling corr (window=60 months):")
print(corr_60.rename(index=names, columns=names).round(2).to_string())

print("\n[WARN] pairs with corr > 0.90 (pool redundancy):")
for i in range(len(cols)):
    for j in range(i + 1, len(cols)):
        a, b = cols[i], cols[j]
        f, r6 = corr_full.loc[a, b], corr_60.loc[a, b]
        if f > 0.90 or r6 > 0.90:
            print("     {} ~ {}: full={:.2f}, 60M={:.2f}".format(names[a], names[b], f, r6))

# ---- 8) 图（服务结论：累计净值 + 相关热力图）----
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    figdir = Path(__file__).resolve().parents[1] / "research" / "figures"
    figdir.mkdir(parents=True, exist_ok=True)

    # 图 1：累计净值（首月=1，对数轴）
    nav = mpx / mpx.apply(lambda s: s.loc[s.first_valid_index()])
    fig, ax = plt.subplots(figsize=(11, 6))
    for c in cols:
        ax.plot(nav.index.to_timestamp(), nav[c], label=names.get(c, c), lw=1.2)
    ax.set_yscale("log")
    ax.set_title("纯 size 池累计净值（月末收盘，首月=1，对数轴；2010-01 至 2026-06）")
    ax.set_ylabel("NAV (log)")
    ax.legend(ncol=2, fontsize=9)
    ax.grid(alpha=0.3)
    f1 = figdir / "eda_size_nav.png"
    fig.savefig(f1, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 图 2：全样本相关热力图
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(corr_full.values, vmin=0.3, vmax=1.0, cmap="RdYlGn_r")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([names.get(c, c) for c in cols], rotation=45, ha="right")
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels([names.get(c, c) for c in cols])
    for i in range(len(cols)):
        for j in range(len(cols)):
            ax.text(j, i, "{:.2f}".format(corr_full.values[i, j]),
                    ha="center", va="center", fontsize=8)
    ax.set_title("月收益全样本相关矩阵（2010-2026.06，pairwise）")
    fig.colorbar(im, shrink=0.8)
    f2 = figdir / "eda_size_corr.png"
    fig.savefig(f2, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print("\n[OK] figure: {}".format(f1))
    print("[OK] figure: {}".format(f2))
except ImportError:
    print("\n[WARN] matplotlib 不可用，仅输出表格")
