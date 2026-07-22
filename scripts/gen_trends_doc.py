"""生成 market_sentiment_monthly 详细趋势文档（Markdown）。
输出到 research/market_sentiment_trends.md
"""
import sys, io
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from config.database import engine

OUT_PATH = ROOT / "research" / "market_sentiment_trends.md"

# 读取全A维度数据
df = pd.read_sql("""
    SELECT * FROM panel_market_sentiment_monthly
    WHERE dimension_type = 'all'
    ORDER BY trade_date
""", engine)
df["trade_date"] = pd.to_datetime(df["trade_date"])
df["year"] = df["trade_date"].dt.year
df["month"] = df["trade_date"].dt.month

# 年频统计
yearly = df.groupby("year")

buf = io.StringIO()
w = buf.write

w("""# market_sentiment_monthly 逐列趋势分析（2010-2026）

> 数据来源：`panel_market_sentiment_monthly`，dimension_type='all'（全A，000985.CSI）
> 生成日期：2026-07-07
> 口径说明：所有指标基于\"当时可知\"的回看窗口计算，无未来函数。
> north_money 已归一化到万元（2024-08-19 tushare接口跳变修复）。

---

## A股历史阶段速查

| 年份 | 阶段 | 核心特征 |
|------|------|---------|
| 2010 | 四万亿后震荡 | 刺激退出+地产调控，全年下跌 |
| 2011-2012 | 慢熊 | 通胀+紧缩+欧债危机，连跌两年 |
| 2013 | 结构性行情 | 创业板元年，主板震荡 |
| 2014 H2-2015 H1 | 杠杆牛 | 两融爆发+场外配资，指数翻倍 |
| 2015 H2 | 股灾 | 去杠杆崩盘+国家队救市 |
| 2016 | 熔断+修复 | 年初熔断，全年慢修复 |
| 2017 | 漂亮50 | 蓝筹白马单边上涨，小票阴跌 |
| 2018 | 贸易战熊市 | 全年单边下跌，年末极端低估 |
| 2019 | 复苏 | 流动性宽松+科创板上线 |
| 2020 | 疫情V型 | 春节暴跌→全球放水→反弹 |
| 2021 | 结构分化 | 新能源+周期暴涨，消费崩盘 |
| 2022 | 全年下跌 | 疫情封控+地产危机+美联储加息 |
| 2023 | 弱复苏 | 预期落空，冲高回落 |
| 2024 | 政策逆转 | 9.24政策组合拳，年末爆发 |
| 2025-2026 | 牛市 | 广谱上涨，成交量持续放大 |

---

""")

# ---- 逐维度分析 ----
DIMS = {
    "价(11)": ["idx_close", "ma60", "ma250", "idx_ret_1m", "idx_ret_3m", "idx_ret_12m",
               "profit_ratio", "up_down_ratio", "pct_above_ma60", "pct_above_ma250", "limit_up_count"],
    "量(6)": ["idx_amount", "turnover_rate_median", "amount_pct_3m", "amount_pct_1y", "amount_gini"],
    "波(6)": ["idx_volatility_20", "idx_volatility_60", "max_drawdown_1y",
               "avg_correlation", "cross_sectional_vol", "downside_vol_ratio"],
    "估值(7)": ["pe_ttm_median", "pb_median", "dv_ttm_median",
                "pe_pct_5y", "pb_pct_5y", "pe_dispersion", "pb_pe_divergence"],
    "资金(7)": ["north_money", "margin_balance", "net_inflow_ratio",
                "inflow_direction_pct", "inflow_stability", "inflow_breadth", "institutional_pct"],
}

for dim_name, cols in DIMS.items():
    w(f"## {dim_name}\n\n")

    # 年频表
    available = [c for c in cols if c in df.columns]
    if not available:
        continue

    yt = yearly[available].mean()
    # 关键年份行
    key_years = [2010, 2011, 2014, 2015, 2016, 2017, 2018, 2020, 2021, 2022, 2024, 2025, 2026]
    key_rows = yt[yt.index.isin(key_years)]

    w("### 年度均值\n\n")
    w("| 年份 | " + " | ".join(available) + " |\n")
    w("|------|" + "|".join(["------" for _ in available]) + "|\n")
    for yr in key_years:
        if yr not in yt.index:
            continue
        vals = []
        for c in available:
            v = yt.loc[yr, c]
            if pd.isna(v):
                vals.append("-")
            elif abs(v) > 1e8:
                vals.append(f"{v/1e8:.1f}亿")
            elif abs(v) > 1e4:
                vals.append(f"{v/1e4:.1f}万")
            elif abs(v) < 0.01 and v != 0:
                vals.append(f"{v:.4f}")
            elif abs(v) < 1:
                vals.append(f"{v:.3f}")
            else:
                vals.append(f"{v:.2f}")
        w("| " + str(yr) + " | " + " | ".join(vals) + " |\n")

    # 逐列详细趋势
    w("\n### 逐列趋势\n\n")
    for col in available:
        yr_mean = yearly[col].mean()
        valid = yr_mean.dropna()
        if len(valid) < 3:
            w(f"**{col}**：数据不足\n\n")
            continue

        all_time_high_yr = valid.idxmax()
        all_time_low_yr = valid.idxmin()
        recent = valid.tail(3)

        w(f"**{col}**\n")
        w(f"- 全周期范围：{valid.min():.4g} ({int(all_time_low_yr)}) ~ {valid.max():.4g} ({int(all_time_high_yr)})\n")
        w(f"- 近3年趋势：{', '.join(f'{int(yr)}={v:.3g}' for yr, v in recent.items())}\n")

        # 每个关键年份的值
        w("- 关键年份：")
        highlights = []
        for yr in [2015, 2018, 2020, 2022, 2024, 2026]:
            if yr in valid.index:
                highlights.append(f"{yr}={valid[yr]:.4g}")
        w(" | ".join(highlights) + "\n\n")

    w("---\n\n")

# ---- 全A指数月度收盘 ----
w("""## 附录A：全A指数月度收盘价 (idx_close, 000985.CSI)

| 年\\月 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 |
|--------|----|----|----|----|----|----|----|----|----|----|----|----|
""")
for yr in sorted(df["year"].unique()):
    row_vals = []
    for m in range(1, 13):
        sub = df[(df["year"] == yr) & (df["month"] == m)]
        if not sub.empty:
            row_vals.append(f"{sub['idx_close'].iloc[-1]:.0f}")
        else:
            row_vals.append("-")
    w(f"| {yr} | " + " | ".join(row_vals) + " |\n")

w("""
---

## 附录B：PE中位数月度序列 (pe_ttm_median)

""")
for yr in sorted(df["year"].unique()):
    row_vals = []
    for m in range(1, 13):
        sub = df[(df["year"] == yr) & (df["month"] == m)]
        if not sub.empty:
            v = sub["pe_ttm_median"].iloc[-1]
            if pd.notna(v):
                row_vals.append(f"{v:.1f}")
            else:
                row_vals.append("-")
        else:
            row_vals.append("-")
    if any(v != "-" for v in row_vals):
        w(f"| {yr} | " + " | ".join(row_vals) + " |\n")

w("""
---

## 附录C：两融余额月度序列 (margin_balance, 亿元)

""")
for yr in sorted(df["year"].unique()):
    row_vals = []
    for m in range(1, 13):
        sub = df[(df["year"] == yr) & (df["month"] == m)]
        if not sub.empty:
            v = sub["margin_balance"].iloc[-1]
            if pd.notna(v):
                row_vals.append(f"{v/1e8:.0f}")
            else:
                row_vals.append("-")
        else:
            row_vals.append("-")
    if any(v != "-" for v in row_vals):
        w(f"| {yr} | " + " | ".join(row_vals) + " |\n")

# Write to file
content = buf.getvalue()
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(content, encoding="utf-8")
print(f"已生成: {OUT_PATH} ({len(content)} 字符)")
