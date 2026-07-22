"""从 ETF 名称解析跟踪指数 + 申万一级分类（与 panel_stock_daily 对齐）。

输出：sw_l1_code, sw_l1_name, style_cap, style_type, sector_group
用法：python scripts/parse_etf_index.py > logs/parse_etf_index.txt 2>&1
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from config.database import engine

OUT = sys.stdout

# ===== 0. 数据 =====
fund_basic = pd.read_sql("SELECT * FROM fund_basic", engine)
stock_etf = fund_basic[
    (fund_basic["fund_type"] == "股票型") &
    (fund_basic["invest_type"].isin(["被动指数型", "增强指数型"]))
].copy()

# ===== 1. 分类映射见 config/etf_universe.py =====

# ===== 2. 批量解析（调用 config/etf_universe） =====
from config.etf_universe import classify_etf, extract_index_name

results = []
for _, row in stock_etf.iterrows():
    name = row["name"]
    idx = extract_index_name(name)
    code, sw_name, cap, sty, group = classify_etf(idx or name)
    results.append({
        "ts_code": row["ts_code"],
        "name": name,
        "extracted_index": idx,
        "sw_l1_code": code,
        "sw_l1_name": sw_name,
        "style_cap": cap,
        "style_type": sty,
        "sector_group": group,
    })

df = pd.DataFrame(results)

# ===== 4. 输出 =====
OUT.write(f"股票型被动/增强指数 ETF: {len(stock_etf)} 只\n")
OUT.write(f"\n解析结果: {len(df)} 只\n")

OUT.write("\n--- sw_l1_name 分布 ---\n")
OUT.write(df["sw_l1_name"].value_counts().to_string())

OUT.write("\n\n--- style_cap 分布 ---\n")
OUT.write(df["style_cap"].value_counts().to_string())

OUT.write("\n\n--- style_type 分布 ---\n")
OUT.write(df["style_type"].value_counts().to_string())

OUT.write("\n\n--- sector_group 分布 ---\n")
OUT.write(df["sector_group"].value_counts().to_string())

unknown = df[(df["sw_l1_name"] == "other") | (df["style_cap"] == "unknown")]
OUT.write(f"\n\n--- 未识别: {len(unknown)} 只 ({len(unknown)/len(df)*100:.1f}%) ---\n")
OUT.write(unknown[["ts_code", "name", "extracted_index"]].to_string(max_rows=50, index=False))

# 各 SW 行业的 ETF 样本
for sw in sorted(df[df["sw_l1_code"] != ""]["sw_l1_name"].unique()):
    sample = df[df["sw_l1_name"] == sw].head(5)
    OUT.write(f"\n\n--- {sw} 样本 ---\n")
    OUT.write(sample[["ts_code", "name", "extracted_index"]].to_string(index=False))

OUT.write("\n完成。\n")
