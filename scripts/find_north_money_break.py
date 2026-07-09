"""精确定位 moneyflow_hsgt.north_money 单位跳变日期。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from config.database import engine

# 全量日频数据
df = pd.read_sql("""
    SELECT trade_date, north_money
    FROM moneyflow_hsgt
    ORDER BY trade_date
""", engine)

# 计算相邻日的量级（log10 绝对值）
df["abs_val"] = df["north_money"].abs()
df["log10"] = np.log10(df["abs_val"].replace(0, np.nan))
df["prev_log10"] = df["log10"].shift(1)
df["log_jump"] = (df["log10"] - df["prev_log10"]).abs()

# 找跳变点：相邻两天 log10 差距 > 1.5（即 30倍以上）
jumps = df[df["log_jump"] > 1.5].copy()
print(f"\n=== log10 跳变 > 1.5 的点 ({len(jumps)} 个) ===")
for _, row in jumps.iterrows():
    prev = df.loc[df["trade_date"] == row["trade_date"] - pd.Timedelta(days=1)]
    if not prev.empty:
        print(f"  {row['trade_date']}: {row['north_money']:>12.2f}  (前一天: {prev.iloc[0]['north_money']:>12.2f})")

# 按月统计量级
df["month"] = df["trade_date"].astype(str).str[:7]
monthly = df.groupby("month").agg(
    n_days=("north_money", "count"),
    min_val=("north_money", "min"),
    max_val=("north_money", "max"),
    median_abs=("abs_val", "median"),
    p90_abs=("abs_val", lambda x: x.quantile(0.9)),
).reset_index()

# 标记量级突变月
monthly["median_order"] = np.log10(monthly["median_abs"])
monthly["jump"] = monthly["median_order"].diff().abs()

print(f"\n=== 月度统计（2024年起，只看量级） ===")
monthly_2024 = monthly[monthly["month"] >= "2024-01"]
for _, row in monthly_2024.iterrows():
    flag = " ← 跳变!" if row["jump"] > 1.0 else ""
    print(f"  {row['month']}: days={int(row['n_days'])}, "
          f"median≈{10**row['median_order']:,.0f}, "
          f"max={row['max_val']:,.0f}{flag}")

# 找出精确跳变日：第一个 abs(north_money) > 50,000 的日期（旧单位上限~20,000）
threshold = 50000
first_big = df[df["abs_val"] > threshold]
if not first_big.empty:
    print(f"\n=== 第一个 |north_money| > {threshold:,} 的日期 ===")
    first = first_big.iloc[0]
    print(f"  {first['trade_date']}: {first['north_money']:,.2f}")
    # 前后各10天
    idx = df[df["trade_date"] == first["trade_date"]].index[0]
    context = df.iloc[max(0,idx-10):min(len(df),idx+11)]
    print(f"\n  上下文 (±10天):")
    for _, r in context.iterrows():
        marker = " ←←←" if r["trade_date"] == first["trade_date"] else ""
        print(f"    {r['trade_date']}: {r['north_money']:>14,.2f}{marker}")

# 按日顺序打印 2024年所有日值，找模式变化
print(f"\n=== 2024年 逐日 north_money (值>1万的标出) ===")
df2024 = df[(df["trade_date"] >= "2024-01-01") & (df["trade_date"] <= "2024-12-31")]
for _, r in df2024.iterrows():
    flag = " ★" if abs(r["north_money"]) > 30000 else ""
    print(f"  {r['trade_date']}: {r['north_money']:>14,.2f}{flag}")
