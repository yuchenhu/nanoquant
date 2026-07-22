"""market_sentiment_monthly 数据分析：schema 对齐 + 逐列分布 + 历史趋势印证。

用法：python scripts/analyze_sentiment.py > logs/analyze_sentiment.txt 2>&1
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from config.database import engine

OUT = sys.stdout

# ============================================================
# 0. 表结构对比：MySQL 实际 vs Python output_schema
# ============================================================
OUT.write("=" * 80 + "\n")
OUT.write("0. Schema 对比：MySQL 实际列 vs Python output_schema\n")
OUT.write("=" * 80 + "\n")

# MySQL 实际列
mysql_cols = pd.read_sql("SHOW COLUMNS FROM panel_market_sentiment_monthly", engine)
mysql_col_set = set(mysql_cols["Field"].tolist())

# Python 定义的列
from data.panel.market_sentiment_monthly import MarketSentimentMonthlyCalculator
calc = MarketSentimentMonthlyCalculator()
py_cols = set(calc.output_schema.keys())

only_mysql = mysql_col_set - py_cols
only_py = py_cols - mysql_col_set
both = mysql_col_set & py_cols

OUT.write(f"\nMySQL 列数: {len(mysql_col_set)}\n")
OUT.write(f"Python output_schema 列数: {len(py_cols)}\n")
OUT.write(f"共有列: {len(both)}\n")

if only_mysql:
    OUT.write(f"\n⚠️ 仅 MySQL 存在（代码已删但表未删列）:\n")
    for c in sorted(only_mysql):
        info = mysql_cols[mysql_cols["Field"] == c].iloc[0]
        OUT.write(f"  {c}  ({info['Type']}, default={info['Default']})\n")
if only_py:
    OUT.write(f"\n⚠️ 仅 Python 存在（表缺少列）:\n")
    for c in sorted(only_py):
        OUT.write(f"  {c}\n")
if not only_mysql and not only_py:
    OUT.write("\n✓ Schema 完全对齐，无差异\n")

# ============================================================
# 1. 逐列数据质量
# ============================================================
OUT.write("\n" + "=" * 80 + "\n")
OUT.write("1. 逐列 NULL 率 & 取值分布（dimension_type='all'，全局）\n")
OUT.write("=" * 80 + "\n")

ALL_QUERY = """
SELECT * FROM panel_market_sentiment_monthly
WHERE dimension_type = 'all'
ORDER BY trade_date
"""
df_all = pd.read_sql(ALL_QUERY, engine)
df_all["trade_date"] = pd.to_datetime(df_all["trade_date"])
df_all = df_all.sort_values("trade_date").reset_index(drop=True)

# 只分析数值列
num_cols = []
for c in df_all.columns:
    if c in ("trade_date", "dimension_type", "dimension_value"):
        continue
    if df_all[c].dtype in (np.float64, np.int64, np.float32, np.int32):
        num_cols.append(c)

OUT.write(f"\n全A 维度行数: {len(df_all)}, 数值列: {len(num_cols)}\n")
OUT.write(f"日期范围: {df_all['trade_date'].min().strftime('%Y-%m-%d')} ~ {df_all['trade_date'].max().strftime('%Y-%m-%d')}\n\n")

for col in num_cols:
    s = df_all[col]
    null_pct = s.isna().mean() * 100
    finite = s[np.isfinite(s)]
    
    if len(finite) == 0:
        OUT.write(f"  [{col}] ❌ 全 NULL/非有限 (100%)\n")
        continue
    
    # 唯一值数
    n_unique = finite.nunique()
    # 基本统计
    p01, p25, p50, p75, p99 = np.percentile(finite, [1, 25, 50, 75, 99])
    
    flag = ""
    if null_pct > 50:
        flag = " ⚠️ NULL>50%"
    elif n_unique <= 5 and len(finite) > 20:
        flag = f" ⚠️ 仅{n_unique}个离散值"
    
    OUT.write(
        f"  [{col}] null={null_pct:.1f}%  n={len(finite)}  "
        f"unique={n_unique}  "
        f"P50={p50:.4g}  P1={p01:.4g}  P99={p99:.4g}"
        f"{flag}\n"
    )

# ============================================================
# 2. 特殊关注：ma60/ma120/ma250 对比
# ============================================================
OUT.write("\n" + "=" * 80 + "\n")
OUT.write("2. MA 列专项检查\n")
OUT.write("=" * 80 + "\n")

for col in ["ma60", "ma120", "ma250"]:
    if col in df_all.columns:
        s = df_all[col]
        null_pct = s.isna().mean() * 100
        valid = s.dropna()
        OUT.write(f"  {col}: null={null_pct:.1f}%  "
                  f"valid={len(valid)}  "
                  f"range=[{valid.min():.1f}, {valid.max():.1f}]\n")
    else:
        OUT.write(f"  {col}: 列不存在于表中\n")

# ============================================================
# 3. 全A维度 逐列 2010-2026 趋势简要
# ============================================================
OUT.write("\n" + "=" * 80 + "\n")
OUT.write("3. 全A 维度 逐列 2010-2026 趋势（年频均值）\n")
OUT.write("=" * 80 + "\n")

df_all["year"] = df_all["trade_date"].dt.year
yearly = df_all.groupby("year")[num_cols].mean()

# 打印年频表（转置方便阅读）
OUT.write("\n年份 × 列 年均值矩阵（部分列用于整体判断）:\n")
# 选代表性列
rep_cols = [c for c in num_cols if c in yearly.columns]
pd.set_option('display.max_columns', 20)
pd.set_option('display.width', 300)
pd.set_option('display.max_rows', 50)
OUT.write(yearly[rep_cols].round(4).to_string())
OUT.write("\n")

# ============================================================
# 4. 与A股历史阶段印证
# ============================================================
OUT.write("\n" + "=" * 80 + "\n")
OUT.write("4. 关键指标与 A 股历史阶段印证\n")
OUT.write("=" * 80 + "\n")

# 选出几个关键指标看年度趋势
key_cols = [
    "idx_close", "idx_ret_12m", "pe_ttm_median", "pe_pct_5y",
    "turnover_rate_median", "idx_volatility_60", "max_drawdown_1y",
    "avg_correlation", "north_money", "margin_balance",
    "pct_above_ma60", "profit_ratio", "net_inflow_ratio",
]

OUT.write("\n关键列 年统计 (mean / min / max / last):\n\n")
for col in key_cols:
    if col not in df_all.columns:
        continue
    grp = df_all.groupby("year")[col]
    yearly_stats = pd.DataFrame({
        "mean": grp.mean(),
        "min": grp.min(),
        "max": grp.max(),
        "last": df_all.groupby("year")[col].last(),
    })
    # 只打印有数据的年份
    has_data = yearly_stats["mean"].notna()
    if has_data.any():
        OUT.write(f"\n--- {col} ---\n")
        OUT.write(yearly_stats[has_data].round(4).to_string())
        OUT.write("\n")

# ============================================================
# 5. 各指数维度对比
# ============================================================
OUT.write("\n" + "=" * 80 + "\n")
OUT.write("5. 各指数维度对比（最新一年均值）\n")
OUT.write("=" * 80 + "\n")

df_idx = pd.read_sql(
    "SELECT * FROM panel_market_sentiment_monthly WHERE dimension_type = 'index' ORDER BY trade_date, dimension_value",
    engine,
)
df_idx["trade_date"] = pd.to_datetime(df_idx["trade_date"])
latest_year = df_idx["trade_date"].dt.year.max()
df_recent = df_idx[df_idx["trade_date"].dt.year == latest_year]

for dim_val in sorted(df_recent["dimension_value"].unique()):
    sub = df_recent[df_recent["dimension_value"] == dim_val]
    OUT.write(f"\n  {dim_val}: {len(sub)} 个月\n")
    for col in ["pe_ttm_median", "idx_ret_12m", "turnover_rate_median", "profit_ratio"]:
        if col in sub.columns:
            s = sub[col].dropna()
            if len(s) > 0:
                OUT.write(f"    {col}: mean={s.mean():.4g}  last={s.iloc[-1]:.4g}\n")

OUT.write("\n分析完成。\n")
