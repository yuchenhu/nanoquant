"""探查 fund_basic / fund_daily 等表的实际数据分布，为 ETF 维度映射做准备。
用法：python scripts/eda_etf_fields.py > logs/eda_etf_fields.txt 2>&1
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from config.database import engine

OUT = sys.stdout

# ============================================================
# 1. fund_basic 表结构和分布
# ============================================================
OUT.write("=" * 70 + "\n")
OUT.write("1. fund_basic 表结构\n")
OUT.write("=" * 70 + "\n")
cols = pd.read_sql("SHOW COLUMNS FROM fund_basic", engine)
OUT.write(cols[["Field", "Type"]].to_string(index=False))
OUT.write(f"\n\n总行数: {pd.read_sql('SELECT COUNT(*) FROM fund_basic', engine).iloc[0,0]}\n")

OUT.write("\n--- fund_type 分布 ---\n")
ft = pd.read_sql("SELECT fund_type, COUNT(*) as n FROM fund_basic GROUP BY fund_type ORDER BY n DESC", engine)
OUT.write(ft.to_string(index=False))

OUT.write("\n\n--- invest_type 分布 ---\n")
it = pd.read_sql("SELECT invest_type, COUNT(*) as n FROM fund_basic GROUP BY invest_type ORDER BY n DESC", engine)
OUT.write(it.to_string(index=False))

OUT.write("\n\n--- market 分布 ---\n")
mk = pd.read_sql("SELECT market, COUNT(*) as n FROM fund_basic GROUP BY market ORDER BY n DESC", engine)
OUT.write(mk.to_string(index=False))

OUT.write("\n\n--- management 管理费分布 ---\n")
OUT.write("\n\n--- management (管理费) 样本 ---\n")
sample_fee = pd.read_sql("SELECT management, COUNT(*) as n FROM fund_basic GROUP BY management ORDER BY n DESC LIMIT 15", engine)
OUT.write(sample_fee.to_string(index=False))

# ============================================================
# 2. 场内 ETF (market='E') 专项分析
# ============================================================
OUT.write("\n\n" + "=" * 70 + "\n")
OUT.write("2. 场内 ETF (market='E') 专项\n")
OUT.write("=" * 70 + "\n")

df_etf = pd.read_sql("SELECT * FROM fund_basic WHERE market='E'", engine)
OUT.write(f"场内基金总数: {len(df_etf)}\n")

# 列出所有列名，看哪些列可能有分类信息
OUT.write("\n--- 所有非空列（看哪些有分类价值）---\n")
for col in df_etf.columns:
    null_pct = df_etf[col].isna().mean() * 100
    n_unique = df_etf[col].nunique()
    if n_unique <= 30 and null_pct < 90:
        OUT.write(f"  {col:20s}  null={null_pct:5.1f}%  unique={n_unique:4d}")
        if n_unique <= 15:
            OUT.write("  ★ 可能是分类列")
        OUT.write("\n")

# 看 fund_type 和 invest_type 的交叉
OUT.write("\n--- fund_type × invest_type 交叉（场内ETF）---\n")
cross = df_etf.groupby(["fund_type", "invest_type"]).size().unstack(fill_value=0)
OUT.write(cross.to_string())

# 看 benchmark 字段（可能包含跟踪指数）
OUT.write("\n\n--- benchmark 样本（前30条）---\n")
if "benchmark" in df_etf.columns:
    bm = df_etf[["ts_code", "name", "benchmark"]].dropna(subset=["benchmark"])
    OUT.write(bm.head(30).to_string(index=False))
    OUT.write(f"\n... 共 {len(bm)} 条有 benchmark\n")
else:
    OUT.write("fund_basic 表中没有 benchmark 列\n")

# 看 name 字段样本，手动判断行业
OUT.write("\n\n--- ETF name 样本（前50条）---\n")
if "name" in df_etf.columns:
    OUT.write(df_etf[["ts_code", "name", "fund_type", "invest_type"]].head(50).to_string(index=False))

# 看 underlying 或 index 相关列
idx_cols = [c for c in df_etf.columns if any(k in str(c).lower() for k in ["index", "underly", "bench", "track"])]
if idx_cols:
    OUT.write(f"\n\n--- 跟踪指数相关列: {idx_cols} ---\n")
    for col in idx_cols:
        sample = df_etf[[col]].dropna().head(10)
        OUT.write(f"\n  {col}:\n")
        OUT.write(sample.to_string(index=False))

# ============================================================
# 3. 与 fund_daily 交叉看可交易ETF
# ============================================================
OUT.write("\n\n" + "=" * 70 + "\n")
OUT.write("3. fund_daily 近期有成交的 ETF（过滤僵尸）\n")
OUT.write("=" * 70 + "\n")

recent = pd.read_sql("""
    SELECT d.ts_code, b.name, b.fund_type, b.invest_type,
           MAX(d.amount) as max_amount_30d,
           COUNT(*) as n_days_30d
    FROM fund_daily d
    JOIN fund_basic b ON d.ts_code = b.ts_code
    WHERE d.trade_date >= '2026-06-01'
    GROUP BY d.ts_code, b.name, b.fund_type, b.invest_type
    HAVING MAX(d.amount) > 0
    ORDER BY max_amount_30d DESC
""", engine)
OUT.write(f"\n近30天有成交的ETF: {len(recent)} 只\n")
OUT.write(recent.head(40).to_string(index=False))

# 按 fund_type 汇总
OUT.write("\n\n--- 按 fund_type 汇总（近30天活跃ETF）---\n")
active_summary = recent.groupby("fund_type").agg(
    count=("ts_code", "count"),
    max_amount=("max_amount_30d", "max"),
).sort_values("count", ascending=False)
OUT.write(active_summary.to_string())

# ============================================================
# 4. 跟踪指数 → fund_basic 的关联方式
# ============================================================
OUT.write("\n\n" + "=" * 70 + "\n")
OUT.write("4. 如何关联 ETF → 跟踪指数\n")
OUT.write("=" * 70 + "\n")

# 如果有 index_code 或 similar 列
for col in ["index_code", "underlying_index", "track_index", "idx_code"]:
    if col in df_etf.columns:
        sample = df_etf[["ts_code", "name", col]].dropna(subset=[col]).head(20)
        OUT.write(f"\n{col} (前20):\n")
        OUT.write(sample.to_string(index=False))

OUT.write("\n完成。\n")
