import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config.database import engine

# 查看股票型 ETF 的分类情况
df = pd.read_sql("""
    SELECT ts_code, name, sw_l1_name, style_cap, style_type, sector_group,
           close, unit_nav, discount_rate, amount, fund_size
    FROM panel_fund_daily
    WHERE trade_date = '2024-05-06'
      AND fund_type = '股票型'
    LIMIT 10
""", engine)
print(df.to_string())
print()

# sector_group 分布
df2 = pd.read_sql("""
    SELECT sector_group, COUNT(*) n FROM panel_fund_daily
    WHERE trade_date = '2024-05-06'
    AND fund_type = '股票型'
    AND invest_type = '被动指数型'
    GROUP BY sector_group ORDER BY n DESC
""", engine)
print("=== 股票型被动指数 ETF sector_group 分布 ===")
print(df2.to_string(index=False))
