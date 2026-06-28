"""DQC 审计脚本：扫描接入层所有表，输出到 audit_result.txt。

检查项：
1. 各表行数 + 时间跨度
2. stock_daily 年度覆盖度（每年交易日数 + 日均股票数）
3. 行情异常值（价格<=0、pct_chg越界、成交额为负）
4. 关键列 NULL 占比
5. 财务表各报告期行数
6. adj_factor 合理性
7. index_daily / fund_daily 年度覆盖度
8. stock_st / suspend 异常
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
    print(msg)


def section(title):
    w("\n" + "=" * 72)
    w(title)
    w("=" * 72)


def q(sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


# ── 1. 各表总行数 + 时间跨度 ──
section("1. 各表行数 + 时间跨度")
checks = [
    ("stock_daily", "trade_date"), ("stock_daily_basic", "trade_date"),
    ("adj_factor", "trade_date"), ("moneyflow", "trade_date"),
    ("stock_weekly", "trade_date"), ("stock_monthly", "trade_date"),
    ("fund_daily", "trade_date"), ("fund_adj", "trade_date"), ("fund_share", "trade_date"),
    ("fund_nav", "nav_date"), ("fund_basic", "found_date"),
    ("index_daily", "trade_date"), ("index_weight", "trade_date"),
    ("index_dailybasic", "trade_date"), ("index_basic", "list_date"),
    ("sw_daily", "trade_date"),
    ("income", "end_date"), ("balancesheet", "end_date"), ("cashflow", "end_date"),
    ("disclosure_date", "end_date"),
    ("dividend", "ex_date"),
    ("stock_st", "trade_date"), ("suspend", "trade_date"),
    ("stock_basic", ""), ("trade_cal", ""),
    ("namechange", "change_date"),
    ("top10_holders", "ann_date"), ("top10_floatholders", "ann_date"),
    ("pledge_stat", "end_date"),
    ("margin", "trade_date"), ("margin_detail", "trade_date"),
    ("moneyflow_hsgt", "trade_date"), ("hsgt_top10", "trade_date"),
    ("ggt_top10", "trade_date"),
    ("limit_list_d", "trade_date"), ("limit_list", "trade_date"),
    ("stk_limit", "trade_date"),
    ("daily_basic", "trade_date"),
    ("new_share", "ipo_date"), ("share_float", "ann_date"),
]
w(f"{'表':<24}{'行数':>10}{'最早':>10}{'最晚':>10}")
w("-" * 54)
for tbl, col in checks:
    try:
        if col:
            r = q(f"SELECT COUNT(*), MIN(`{col}`), MAX(`{col}`) FROM `{tbl}`")[0]
            cnt, mn, mx = r[0], r[1], r[2]
        else:
            r = q(f"SELECT COUNT(*) FROM `{tbl}`")[0]
            cnt, mn, mx = r[0], "", ""
        flag = "  [EMPTY]" if cnt == 0 else ""
        w(f"{tbl:<24}{cnt:>10}{str(mn):>12}{str(mx):>12}{flag}")
    except Exception as e:
        w(f"{tbl:<24} 表不存在或查询失败: {e}")

# ── 2. stock_daily 年度覆盖度 ──
section("2. stock_daily 年度覆盖度（每年交易日 + 日均股票数）")
w(f"{'年':<8}{'交易日':>10}{'总行数':>12}{'日均股':>8}{'标志'}")
w("-" * 48)
try:
    rows = q("""
        SELECT LEFT(trade_date,4) AS y, COUNT(DISTINCT trade_date) AS days, COUNT(*) AS n
        FROM stock_daily GROUP BY LEFT(trade_date,4) ORDER BY y
    """)
    for r in rows:
        y, days, n = r[0], r[1], r[2]
        avg = round(n / days) if days else 0
        flag = ""
        if days < 200 or days > 260:
            flag += " [WARN:交易日异常]"
        if avg < 1000:
            flag += " [WARN:股票偏少]"
        w(f"{y:<8}{days:>10}{n:>12}{avg:>8}{flag}")
except Exception as e:
    w(f"  查询失败: {e}")

# ── 3. 行情异常值 ──
section("3. stock_daily 异常值检测")
checks3 = [
    ("close <= 0", "收盘价<=0"),
    ("`open` <= 0 OR high <= 0 OR low <= 0", "OHLC<=0"),
    ("high < low", "最高<最低"),
    ("pct_chg > 30 OR pct_chg < -30", "涨跌幅>±30%"),
    ("vol < 0 OR amount < 0", "成交量额为负"),
    ("close IS NULL", "收盘价NULL"),
    ("vol IS NULL", "成交量NULL"),
]
for cond, desc in checks3:
    try:
        n = q(f"SELECT COUNT(*) FROM stock_daily WHERE {cond}")[0][0]
        flag = "  [WARN]" if n > 0 else "  [OK]"
        w(f"  {desc:<24} {n:>8} 行{flag}")
    except Exception as e:
        w(f"  {desc}: {e}")

# ── 4. 关键列 NULL 占比 ──
section("4. 关键列 NULL 占比")
null_checks = [
    ("stock_daily", "close"), ("stock_daily", "vol"),
    ("stock_daily_basic", "total_mv"), ("stock_daily_basic", "pe_ttm"),
    ("adj_factor", "adj_factor"),
    ("income", "n_income"), ("income", "revenue"),
    ("fund_daily", "close"), ("index_weight", "weight"),
    ("index_daily", "close"), ("index_dailybasic", "total_mv"),
    ("moneyflow", "net_mf_amount"),
]
for tbl, col in null_checks:
    try:
        r = q(f"SELECT COUNT(*), SUM(CASE WHEN `{col}` IS NULL THEN 1 ELSE 0 END) FROM `{tbl}`")[0]
        total, nulls = r[0], r[1] or 0
        pct = round(100 * nulls / total, 2) if total else 0
        flag = ""
        if col not in ("pe_ttm", "pe") and pct > 5:
            flag = "  [WARN:NULL偏高]"
        w(f"  {tbl}.{col:<16} NULL {pct:>5}% ({nulls}/{total}){flag}")
    except Exception as e:
        w(f"  {tbl}.{col}: {e}")

# ── 5. 财务表各报告期行数 ──
section("5. 财务表 income 各报告期行数")
try:
    rows = q("""
        SELECT end_date, COUNT(*) FROM income
        WHERE LEFT(end_date,4) >= '2010'
        GROUP BY end_date ORDER BY end_date
    """)
    if not rows:
        w("  无数据")
    for r in rows:
        ed, n = r[0], r[1]
        flag = ""
        if str(ed)[-4:] == "1231" and n < 3000:
            flag = " [WARN:年报行数偏少]"
        w(f"  {ed}: {n} 行{flag}")
except Exception as e:
    w(f"  income: {e}")

# ── 6. adj_factor 合理性 ──
section("6. adj_factor 分布")
try:
    rr = q("SELECT MIN(adj_factor), MAX(adj_factor), ROUND(AVG(adj_factor),3) FROM adj_factor")[0]
    neg = q("SELECT COUNT(*) FROM adj_factor WHERE adj_factor<=0")[0][0]
    w(f"  范围 [{rr[0]}, {rr[1]}], 均值 {rr[2]}")
    w(f"  <=0 的异常行: {neg}{'  [WARN]' if neg>0 else '  [OK]'}")
except Exception as e:
    w(f"  adj_factor: {e}")

# ── 7. index_daily + fund_daily 年度覆盖度 ──
section("7. index_daily 年度覆盖度")
try:
    rows = q("""
        SELECT LEFT(trade_date,4) AS y, COUNT(DISTINCT ts_code) AS n_codes,
               COUNT(DISTINCT trade_date) AS days, COUNT(*) AS n
        FROM index_daily GROUP BY LEFT(trade_date,4) ORDER BY y
    """)
    w(f"{'年':<8}{'指数数':>8}{'交易日':>8}{'总行数':>10}")
    w("-" * 38)
    for r in rows:
        w(f"{r[0]:<8}{r[1]:>8}{r[2]:>8}{r[3]:>10}")
except Exception as e:
    w(f"  index_daily: {e}")

section("8. fund_daily 年度覆盖度")
try:
    rows = q("""
        SELECT LEFT(trade_date,4) AS y, COUNT(DISTINCT ts_code) AS n_codes,
               COUNT(DISTINCT trade_date) AS days, COUNT(*) AS n
        FROM fund_daily GROUP BY LEFT(trade_date,4) ORDER BY y
    """)
    w(f"{'年':<8}{'基金数':>8}{'交易日':>8}{'总行数':>10}")
    w("-" * 38)
    for r in rows:
        w(f"{r[0]:<8}{r[1]:>8}{r[2]:>8}{r[3]:>10}")
except Exception as e:
    w(f"  fund_daily: {e}")

# ── 9. 各表按年行数分布 (快速看哪些年缺数) ──
section("9. 核心表按年行数分布")
year_tables = [
    ("stock_daily", "trade_date"),
    ("stock_daily_basic", "trade_date"),
    ("adj_factor", "trade_date"),
    ("moneyflow", "trade_date"),
    ("index_daily", "trade_date"),
    ("fund_daily", "trade_date"),
    ("income", "end_date"),
    ("balancesheet", "end_date"),
]
for tbl, col in year_tables:
    try:
        rows = q(f"""
            SELECT LEFT(`{col}`,4) AS y, COUNT(*) AS n
            FROM `{tbl}` GROUP BY LEFT(`{col}`,4) ORDER BY y
        """)
        w(f"\n  [{tbl}]")
        w(f"  {'年':<8}{'行数':>10}")
        for r in rows:
            w(f"  {r[0]:<8}{r[1]:>10}")
    except Exception as e:
        w(f"  [{tbl}] 查询失败: {e}")

# ── 10. trade_cal 覆盖 ──
section("10. trade_cal 覆盖")
try:
    rows = q("SELECT MIN(cal_date), MAX(cal_date), COUNT(*), SUM(is_open) FROM trade_cal")[0]
    w(f"  日历范围: {rows[0]} ~ {rows[1]}")
    w(f"  总日历日: {rows[2]}, 交易日: {rows[3]}")
except Exception as e:
    w(f"  trade_cal: {e}")

# 输出
result = "\n".join(lines)
try:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(result, encoding="utf-8")
    print(f"\n已写入 {OUT}")
except Exception as e:
    print(f"\n写入文件失败: {e}")
    # fallback 写入根目录
    fallback = ROOT / "_audit.txt"
    fallback.write_text(result, encoding="utf-8")
    print(f"已写入 fallback: {fallback}")
