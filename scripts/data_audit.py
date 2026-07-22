"""数据探查：回补完成后体检各表分布，找不合理的数据。

检查项（按表分类）：
1. 行情类(stock_daily等)：年度行数趋势、价格/成交额异常值、NULL 占比、pct_chg 越界
2. 财务类：各报告期行数、负营收/极端值
3. ETF：fund_daily 行数、规模分布
4. 覆盖度：每年交易日数、每日股票数是否合理
5. 全表：行数为 0 的表、NULL 比例异常的关键列

结果写 logs/data_audit.txt（同时打印）。只读不改。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from config.database import engine

OUT = ROOT / "logs" / "data_audit.txt"
lines = []


def w(msg=""):
    lines.append(str(msg))


def q(sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


def section(title):
    w("\n" + "=" * 72)
    w(title)
    w("=" * 72)


# ── 1. 各表总行数 + 时间跨度 ──
section("1. 各表行数 + 时间跨度")
checks = [
    ("stock_daily", "trade_date"), ("stock_daily_basic", "trade_date"),
    ("adj_factor", "trade_date"), ("moneyflow", "trade_date"),
    ("stock_weekly", "trade_date"), ("stock_monthly", "trade_date"),
    ("fund_daily", "trade_date"), ("fund_adj", "trade_date"), ("fund_share", "trade_date"),
    ("index_daily", "trade_date"), ("index_weight", "trade_date"), ("sw_daily", "trade_date"),
    ("income", "end_date"), ("balancesheet", "end_date"), ("cashflow", "end_date"),
    ("disclosure_date", "end_date"), ("dividend", "ex_date"),
    ("stock_st", "trade_date"), ("suspend", "trade_date"),
]
w(f"{'表':<22}{'行数':>10}{'最早':>10}{'最晚':>10}")
w("-" * 52)
for tbl, col in checks:
    try:
        r = q(f"SELECT COUNT(*), MIN({col}), MAX({col}) FROM `{tbl}`")[0]
        cnt, mn, mx = r[0], r[1], r[2]
        flag = "  [EMPTY]" if cnt == 0 else ""
        w(f"{tbl:<22}{cnt:>10}{str(mn):>12}{str(mx):>12}{flag}")
    except Exception as e:
        w(f"{tbl:<22} 查询失败: {e}")

# ── 2. stock_daily 年度覆盖度（每年交易日数 + 日均股票数）──
section("2. stock_daily 年度覆盖度（每年交易日 + 日均股票数）")
w(f"{'年':<8}{'交易日数':>10}{'总行数':>12}{'日均股票':>10}")
w("-" * 40)
rows = q("""
    SELECT LEFT(trade_date,4) AS y, COUNT(DISTINCT trade_date) AS days, COUNT(*) AS n
    FROM stock_daily GROUP BY LEFT(trade_date,4) ORDER BY y
""")
for r in rows:
    y, days, n = r[0], r[1], r[2]
    avg = round(n / days) if days else 0
    # 合理性：交易日 240±15，日均股票数应随年份递增（1400→5400）
    flag = ""
    if days < 200 or days > 260:
        flag += " [WARN:交易日异常]"
    if avg < 1000:
        flag += " [WARN:股票数偏少]"
    w(f"{y:<8}{days:>10}{n:>12}{avg:>10}{flag}")

# ── 3. 行情异常值（价格<=0、pct_chg越界、成交额为负）──
section("3. stock_daily 异常值检测")
checks3 = [
    ("close <= 0", "收盘价<=0"),
    ("`open` <= 0 OR high <= 0 OR low <= 0", "OHLC<=0"),
    ("high < low", "最高<最低"),
    ("pct_chg > 30 OR pct_chg < -30", "涨跌幅>±30%(异常)"),
    ("vol < 0 OR amount < 0", "成交量额为负"),
]
for cond, desc in checks3:
    try:
        n = q(f"SELECT COUNT(*) FROM stock_daily WHERE {cond}")[0][0]
        flag = "  [WARN]" if n > 0 else "  [OK]"
        w(f"  {desc:<24} {n:>8} 行{flag}")
    except Exception as e:
        w(f"  {desc}: {e}")

# ── 4. 关键列 NULL 占比 ──
section("4. 关键列 NULL 占比（应接近 0）")
null_checks = [
    ("stock_daily", "close"), ("stock_daily", "vol"),
    ("stock_daily_basic", "total_mv"), ("stock_daily_basic", "pe_ttm"),
    ("adj_factor", "adj_factor"), ("income", "n_income"),
    ("fund_daily", "close"), ("index_weight", "weight"),
]
for tbl, col in null_checks:
    try:
        r = q(f"SELECT COUNT(*), SUM(CASE WHEN `{col}` IS NULL THEN 1 ELSE 0 END) FROM `{tbl}`")[0]
        total, nulls = r[0], r[1] or 0
        pct = round(100 * nulls / total, 2) if total else 0
        # pe_ttm 亏损股本就 NULL，允许高；其他关键列应接近 0
        flag = ""
        if col not in ("pe_ttm", "pe") and pct > 5:
            flag = "  [WARN:NULL偏高]"
        w(f"  {tbl}.{col:<16} NULL {pct:>5}% ({nulls}/{total}){flag}")
    except Exception as e:
        w(f"  {tbl}.{col}: {e}")

# ── 5. 财务表各报告期行数（应 ~全市场，4000-5500）──
section("5. 财务表 income 各报告期行数（近 5 年年报）")
rows = q("""
    SELECT end_date, COUNT(*) FROM income
    WHERE RIGHT(end_date,4)='1231' AND LEFT(end_date,4) >= '2020'
    GROUP BY end_date ORDER BY end_date
""")
for r in rows:
    ed, n = r[0], r[1]
    flag = " [WARN:行数偏少]" if n < 3000 else ""
    w(f"  年报 {ed}: {n} 行{flag}")

# ── 6. 复权因子合理性（应 >0，多数=1或略大）──
section("6. adj_factor 分布")
try:
    r = q("SELECT MIN(adj_factor), MAX(adj_factor), AVG(adj_factor), COUNT(*) FROM adj_factor WHERE adj_factor<=0")
    rr = q("SELECT MIN(adj_factor), MAX(adj_factor), ROUND(AVG(adj_factor),3) FROM adj_factor")[0]
    neg = q("SELECT COUNT(*) FROM adj_factor WHERE adj_factor<=0")[0][0]
    w(f"  范围 [{rr[0]}, {rr[1]}], 均值 {rr[2]}")
    w(f"  <=0 的异常行: {neg}{'  [WARN]' if neg>0 else '  [OK]'}")
except Exception as e:
    w(f"  adj_factor: {e}")

# 输出
result = "\n".join(lines)
print(result)
OUT.write_text(result, encoding="utf-8")
print(f"\n已写入 {OUT}")
