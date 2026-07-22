"""排查 north_money 2024年跳变：对比 moneyflow_hsgt 原始表。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from config.database import engine

# 1. 查 moneyflow_hsgt 表结构
cols = pd.read_sql("SHOW COLUMNS FROM moneyflow_hsgt", engine)
print("=== moneyflow_hsgt 表结构 ===")
print(cols.to_string(index=False))

# 2. 查 2023-12 到 2024-01 区间的原始数据
df = pd.read_sql("""
    SELECT trade_date, north_money 
    FROM moneyflow_hsgt 
    WHERE trade_date BETWEEN '2023-11-01' AND '2024-02-28'
    ORDER BY trade_date
""", engine)
print(f"\n=== 2023-11 ~ 2024-02 原始数据 ({len(df)} 行) ===")
print(df.to_string(max_rows=60))

# 3. 查全量 north_money 的年度统计
df_all = pd.read_sql("""
    SELECT YEAR(trade_date) as yr, 
           COUNT(*) as n_days,
           MIN(north_money) as min_val,
           MAX(north_money) as max_val,
           AVG(north_money) as avg_val,
           SUM(north_money) as sum_val
    FROM moneyflow_hsgt 
    GROUP BY YEAR(trade_date)
    ORDER BY yr
""", engine)
print(f"\n=== moneyflow_hsgt north_money 年度统计 ===")
print(df_all.to_string(index=False))

# 4. 抽查 2023-12 最后几天和 2024-01 前几天的具体值
print(f"\n=== 2023年12月最后5天 ===")
print(df[df["trade_date"] <= "2023-12-31"].tail(5).to_string(index=False))
print(f"\n=== 2024年1月前5天 ===")
print(df[df["trade_date"] >= "2024-01-01"].head(5).to_string(index=False))
