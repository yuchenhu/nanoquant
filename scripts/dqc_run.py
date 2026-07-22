"""DQC 审计：扫描接入层数据质量，输出到 logs/data_audit.txt"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from config.database import engine

OUT = ROOT / "logs" / "data_audit.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)
lines = []
errors = []


def w(msg=""):
    lines.append(str(msg))
    print(msg)


def q(sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


def safe_section(check_name, fn):
    """Run a check function safely, capturing any errors."""
    try:
        fn()
    except Exception as e:
        err_msg = "[ERROR] {}: {}".format(check_name, str(e)[:200])
        errors.append(err_msg)
        w(err_msg)
        w(traceback.format_exc()[:500])
    # 每个 section 写完就落盘
    with open(str(OUT), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.flush()


# ===== 1. 各表行数 + 时间跨度 =====
def check_tables():
    w("=" * 60)
    w("1. 各表行数 + 时间跨度")
    w("=" * 60)
    checks = [
        ("stock_daily", "trade_date"), ("stock_daily_basic", "trade_date"),
        ("adj_factor", "trade_date"), ("moneyflow", "trade_date"),
        ("stock_weekly", "trade_date"), ("stock_monthly", "trade_date"),
        ("fund_daily", "trade_date"), ("fund_adj", "trade_date"), ("fund_share", "trade_date"),
        ("fund_nav", "nav_date"), ("fund_basic", ""),
        ("index_daily", "trade_date"), ("index_weight", "trade_date"),
        ("index_dailybasic", "trade_date"), ("index_basic", ""),
        ("sw_daily", "trade_date"),
        ("income", "end_date"), ("balancesheet", "end_date"), ("cashflow", "end_date"),
        ("disclosure_date", "end_date"), ("dividend", "ex_date"),
        ("stock_st", "trade_date"), ("suspend", "trade_date"),
        ("stock_basic", ""), ("trade_cal", ""),
        ("namechange", "change_date"),
        ("margin", "trade_date"), ("moneyflow_hsgt", "trade_date"),
        ("limit_list_d", "trade_date"), ("limit_list", "trade_date"),
        ("stk_limit", "trade_date"), ("daily_basic", "trade_date"),
        ("new_share", "ipo_date"), ("share_float", "ann_date"),
        ("top10_holders", "ann_date"), ("top10_floatholders", "ann_date"),
        ("pledge_stat", "end_date"), ("repurchase", "ann_date"),
        ("hsgt_top10", "trade_date"), ("ggt_top10", "trade_date"),
        ("forecast", "end_date"), ("express", "end_date"),
        ("fina_indicator", "end_date"), ("fina_audit", "end_date"),
        ("fina_mainbz", "end_date"),
    ]
    w("{:<24}{:>10}{:>12}{:>12}".format("表", "行数", "最早", "最晚"))
    w("-" * 58)
    for tbl, col in checks:
        try:
            if col:
                r = q("SELECT COUNT(*), MIN(`" + col + "`), MAX(`" + col + "`) FROM `" + tbl + "`")[0]
                cnt, mn, mx = r[0], str(r[1] or ""), str(r[2] or "")
            else:
                r = q("SELECT COUNT(*) FROM `" + tbl + "`")[0]
                cnt, mn, mx = r[0], "", ""
            flag = "  [EMPTY]" if cnt == 0 else ""
            w("{:<24}{:>10}{:>12}{:>12}{}".format(tbl, cnt, mn, mx, flag))
        except Exception as e:
            err = str(e)[:80]
            w("{:<24} 不存在或错误: {}".format(tbl, err))


safe_section("1-各表行数", check_tables)


# ===== 2. stock_daily 年度覆盖度 =====
def check_stock_daily_yearly():
    w("=" * 60)
    w("2. stock_daily 年度覆盖度")
    w("=" * 60)
    w("{:<8}{:>10}{:>12}{:>8}".format("年", "交易日", "总行数", "日均股"))
    rows = q("SELECT LEFT(trade_date,4) y, COUNT(DISTINCT trade_date) d, COUNT(*) n FROM stock_daily GROUP BY y ORDER BY y")
    for r in rows:
        y, d, n = r[0], r[1], r[2]
        avg = round(n / d) if d else 0
        flag = ""
        if d < 200 or d > 260:
            flag = " !交易日"
        if avg < 1000:
            flag += " !股票少"
        w("{:<8}{:>10}{:>12}{:>8}{}".format(y, d, n, avg, flag))


safe_section("2-stock_daily年度", check_stock_daily_yearly)


# ===== 3. 行情异常值 =====
def check_anomalies():
    w("=" * 60)
    w("3. stock_daily 异常值")
    w("=" * 60)
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
        n = q("SELECT COUNT(*) FROM stock_daily WHERE " + cond)[0][0]
        flag = "  [WARN]" if n > 0 else "  [OK]"
        w("  {:<24}{:>8} 行{}".format(desc, n, flag))


safe_section("3-异常值", check_anomalies)


# ===== 4. 关键列 NULL 占比 =====
def check_nulls():
    w("=" * 60)
    w("4. 关键列 NULL 占比")
    w("=" * 60)
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
            r = q("SELECT COUNT(*), SUM(CASE WHEN `" + col + "` IS NULL THEN 1 ELSE 0 END) FROM `" + tbl + "`")[0]
            total, nulls = r[0], r[1] or 0
            pct = round(100 * nulls / total, 2) if total else 0
            flag = ""
            if col not in ("pe_ttm",) and pct > 5:
                flag = "  [WARN:NULL偏高]"
            w("  {}.{:<16} NULL {:>5}% ({}/{}){}".format(tbl, col, pct, nulls, total, flag))
        except Exception as e:
            w("  {}.{}: {}".format(tbl, col, str(e)[:60]))


safe_section("4-NULL占比", check_nulls)


# ===== 5. 财务表各报告期行数 =====
def check_financials():
    w("=" * 60)
    w("5. 财务表各报告期行数")
    w("=" * 60)
    for ftbl in ["income", "balancesheet", "cashflow"]:
        try:
            rows = q("SELECT end_date, COUNT(*) FROM " + ftbl + " WHERE LEFT(end_date,4) >= '2010' GROUP BY end_date ORDER BY end_date")
            if not rows:
                w("  [" + ftbl + "] 无数据")
                continue
            w("  [" + ftbl + "]")
            for r in rows:
                ed, n = r[0], r[1]
                flag = ""
                if str(ed)[-4:] == "1231" and n < 3000:
                    flag = " [年报偏少]"
                w("    {}: {} 行{}".format(ed, n, flag))
        except Exception as e:
            w("  [" + ftbl + "] 查询失败: " + str(e)[:60])


safe_section("5-财务表", check_financials)


# ===== 6. adj_factor 合理性 =====
def check_adj_factor():
    w("=" * 60)
    w("6. adj_factor 分布")
    w("=" * 60)
    rr = q("SELECT MIN(adj_factor), MAX(adj_factor), ROUND(AVG(adj_factor),3) FROM adj_factor")[0]
    neg = q("SELECT COUNT(*) FROM adj_factor WHERE adj_factor<=0")[0][0]
    w("  范围 [{}, {}], 均值 {}".format(rr[0], rr[1], rr[2]))
    flag = "  [WARN]" if neg > 0 else "  [OK]"
    w("  <=0 的异常行: {}{}".format(neg, flag))


safe_section("6-adj_factor", check_adj_factor)


# ===== 7. index_daily / fund_daily 年度覆盖度 =====
def check_index_fund_yearly():
    for title, tbl in [("7. index_daily 年度覆盖度", "index_daily"), ("8. fund_daily 年度覆盖度", "fund_daily")]:
        w("=" * 60)
        w(title)
        w("=" * 60)
        try:
            rows = q("SELECT LEFT(trade_date,4) y, COUNT(DISTINCT ts_code) codes, COUNT(DISTINCT trade_date) days, COUNT(*) n FROM " + tbl + " GROUP BY y ORDER BY y")
            w("{:<8}{:>8}{:>8}{:>10}".format("年", "标的数", "交易日", "总行数"))
            for r in rows:
                w("{:<8}{:>8}{:>8}{:>10}".format(r[0], r[1], r[2], r[3]))
        except Exception as e:
            w("  " + tbl + ": " + str(e)[:60])


safe_section("7-8-指数基金年度", check_index_fund_yearly)


# ===== 9. 核心表按年行数分布 =====
def check_yearly_rows():
    w("=" * 60)
    w("9. 核心表按年行数")
    w("=" * 60)
    for tbl, col in [
        ("stock_daily", "trade_date"),
        ("stock_daily_basic", "trade_date"),
        ("adj_factor", "trade_date"),
        ("moneyflow", "trade_date"),
        ("index_daily", "trade_date"),
        ("fund_daily", "trade_date"),
        ("income", "end_date"),
        ("balancesheet", "end_date"),
        ("cashflow", "end_date"),
        ("index_weight", "trade_date"),
        ("sw_daily", "trade_date"),
        ("fund_adj", "trade_date"),
    ]:
        try:
            rows = q("SELECT LEFT(`" + col + "`,4) y, COUNT(*) n FROM `" + tbl + "` GROUP BY y ORDER BY y")
            w("\n  [" + tbl + "]")
            w("  {:<8}{:>10}".format("年", "行数"))
            for r in rows:
                w("  {:<8}{:>10}".format(r[0], r[1]))
        except Exception as e:
            w("  [" + tbl + "] 查询失败: " + str(e)[:60])


safe_section("9-核心表按年", check_yearly_rows)


# ===== 10. trade_cal =====
def check_trade_cal():
    w("=" * 60)
    w("10. trade_cal")
    w("=" * 60)
    rows = q("SELECT MIN(cal_date), MAX(cal_date), COUNT(*), SUM(is_open) FROM trade_cal")[0]
    w("  范围: {} ~ {}".format(rows[0], rows[1]))
    w("  总日历: {}, 交易日: {}".format(rows[2], rows[3]))


safe_section("10-trade_cal", check_trade_cal)


# ===== 11. 重复行检查 =====
def check_dups():
    w("=" * 60)
    w("11. 重复行检查")
    w("=" * 60)
    dup_tables = [
        ("suspend", "ts_code, trade_date"),
        ("stock_daily", "ts_code, trade_date"),
        ("stock_daily_basic", "ts_code, trade_date"),
        ("adj_factor", "ts_code, trade_date"),
        ("index_daily", "ts_code, trade_date"),
        ("fund_daily", "ts_code, trade_date"),
        ("moneyflow", "ts_code, trade_date"),
    ]
    for tbl, keys in dup_tables:
        try:
            r = q("SELECT COUNT(*) - COUNT(DISTINCT " + keys + ") as dup FROM `" + tbl + "`")[0][0]
            if r > 0:
                w("  [{}] 重复行: {} 条  [WARN]".format(tbl, r))
            else:
                w("  [{}] 无重复  [OK]".format(tbl))
        except Exception as e:
            w("  [{}] 检查失败: {}".format(tbl, str(e)[:60]))


safe_section("11-重复行", check_dups)


# ===== 12. Schema 类型不匹配 =====
w("=" * 60)
w("12. Schema 类型不匹配（已知: moneyflow BIGINT vs DOUBLE）")
w("=" * 60)
w("  moneyflow: buy_sm_vol, sell_sm_vol, buy_md_vol, sell_md_vol, sell_lg_vol, sell_elg_vol, net_mf_vol")
w("  以上列库中为 BIGINT，API 返回 DOUBLE，需 ALTER TABLE 迁移")

# ===== 输出 =====
w("")
w("=" * 60)
w("总错误数: {}".format(len(errors)))
if errors:
    for e in errors:
        w("  " + e)

# 立即写文件，分批追加防止丢失
try:
    result = "\n".join(lines)
    with open(str(OUT), "w", encoding="utf-8") as f:
        f.write(result)
        f.flush()
    print("DQC DONE: " + str(OUT) + " (" + str(len(lines)) + " lines)")
except Exception as exc:
    print("WRITE ERROR: " + str(exc))
