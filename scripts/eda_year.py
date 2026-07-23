"""逐年 EDA：跑完某年 backfill 后，检查各接入层表该年数据质量。

用法：python scripts/eda_year.py 2010
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.database import execute_sql

# 各表 → 日期列映射（用于 WHERE 过滤年份）
TABLE_DATE_COL = {
    "stock_daily": "trade_date",
    "stock_daily_basic": "trade_date",
    "adj_factor": "trade_date",
    "index_daily": "trade_date",
    "fund_daily": "trade_date",
    "fund_adj": "trade_date",
    "fund_nav": "nav_date",
    "fund_share": "trade_date",
    "fund_basic": None,  # 清单表，无日期列
    "moneyflow_hsgt": "trade_date",
    "margin": "trade_date",
    "limit_list_d": "trade_date",
    "suspend": "trade_date",
    "dividend": "ex_date",
    "trade_cal": "cal_date",
    "stock_basic": None,
    "index_basic": None,
    "fund_factor_pro": "trade_date",
    # 财务表（by_period，日期列 end_date）
    "income": "end_date",
    "balancesheet": "end_date",
    "cashflow": "end_date",
    "disclosure_date": "end_date",
}


def eda_year(year: int):
    """对指定年份的接入层表做行数/日期范围检查。"""
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    print(f"\n{'='*70}")
    print(f"  EDA: {year} 年接入层数据质量")
    print(f"{'='*70}")
    print(f"{'table':<25s}  date_range            {'rows':>10s}  status")
    print("-" * 70)

    all_ok = True
    for table, date_col in TABLE_DATE_COL.items():
        try:
            if date_col:
                df = execute_sql(
                    f"SELECT MIN({date_col}) as earliest, MAX({date_col}) as latest, "
                    f"COUNT(*) as cnt FROM {table} "
                    f"WHERE {date_col} >= '{start}' AND {date_col} <= '{end}'"
                )
            else:
                df = execute_sql(f"SELECT COUNT(*) as cnt FROM {table}")
                if not df.empty:
                    r = df.iloc[0]
                    print(f"{table:<25s}  {'N/A':<22s}  {r['cnt']:>10,}  [OK]")
                continue

            if df.empty:
                print(f"{table:<25s}  {'N/A':<22s}  {'0':>10s}  [EMPTY]")
                continue

            r = df.iloc[0]
            cnt = r["cnt"]
            earliest = str(r["earliest"])[:10] if r["earliest"] else "NULL"
            latest = str(r["latest"])[:10] if r["latest"] else "NULL"
            date_range = f"{earliest} ~ {latest}"

            # 判断状态
            if cnt == 0:
                status = "[EMPTY]"
                all_ok = False
            elif cnt > 0 and earliest >= start[:7] and latest <= end[:7]:
                status = "[OK]"
            elif earliest < start[:7]:
                status = f"[WARN: before {year}]"
            else:
                status = "[OK]"

            print(f"{table:<25s}  {date_range:<22s}  {cnt:>10,}  {status}")

        except Exception as ex:
            err_msg = str(ex).split("\n")[0][:60]
            print(f"{table:<25s}  {'ERROR':<22s}  {'-':>10s}  {err_msg}")
            all_ok = False

    print("-" * 70)
    if all_ok:
        print(f"  [OK] {year} 年全部通过")
    else:
        print(f"  [WARN] {year} 年有异常，请检查")
    print("=" * 70)


if __name__ == "__main__":
    y = int(sys.argv[1]) if len(sys.argv) > 1 else 2010
    eda_year(y)