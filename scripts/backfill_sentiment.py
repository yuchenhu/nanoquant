"""逐月回补 panel_market_sentiment_monthly 全量历史。

用法：
  python scripts/backfill_sentiment.py                    # 全量 2010-01 ~ 今
  python scripts/backfill_sentiment.py --start 20200601   # 指定起止
  python scripts/backfill_sentiment.py --test              # 先跑 1 个月验证
"""
import sys
import argparse
import time
import logging
from calendar import monthrange
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from config.database import engine
from data.panel.market_sentiment_monthly import MarketSentimentMonthlyCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backfill_sentiment")


def main():
    parser = argparse.ArgumentParser(description="回补 market_sentiment_monthly 全量历史")
    parser.add_argument("--start", default="20100101", help="起始年月 (YYYYMMDD)")
    parser.add_argument("--end", default=None, help="结束年月 (YYYYMMDD)，默认=当月")
    parser.add_argument("--test", action="store_true", help="测试：只跑 1 个月")
    args = parser.parse_args()

    calc = MarketSentimentMonthlyCalculator(engine=engine)

    if args.test:
        logger.info("=== 测试模式：跑 2021-06 ===")
        t0 = time.time()
        calc.update(start_date="20210601", end_date="20210630")
        elapsed = time.time() - t0
        cnt = pd.read_sql(
            "SELECT COUNT(*) AS c FROM panel_market_sentiment_monthly WHERE trade_date='2021-06-30'",
            engine
        ).iloc[0, 0]
        logger.info(f"2021-06: {cnt} 行, {elapsed:.0f}s")
        # 抽验一条
        row = pd.read_sql(
            "SELECT * FROM panel_market_sentiment_monthly WHERE trade_date='2021-06-30' LIMIT 1",
            engine
        )
        non_null = int(row.notna().sum().iloc[0])
        logger.info(f"第一行非空: {non_null}/{len(row.columns)}")
        return

    # 构建月份列表
    import datetime
    start_y, start_m = int(args.start[:4]), int(args.start[4:6])
    now = datetime.date.today()
    if args.end:
        end_y, end_m = int(args.end[:4]), int(args.end[4:6])
    else:
        end_y, end_m = now.year, now.month

    months = []
    for y in range(start_y, end_y + 1):
        m_start = start_m if y == start_y else 1
        m_end = end_m if y == end_y else 12
        for m in range(m_start, m_end + 1):
            last_day = monthrange(y, m)[1]
            start = f"{y}{m:02d}01"
            end = f"{y}{m:02d}{last_day:02d}"
            months.append((y, m, start, end))

    logger.info(f"共 {len(months)} 个月待处理 ({months[0][2]} ~ {months[-1][2]})")

    failed = []
    t_total = time.time()
    for i, (y, m, start, end) in enumerate(months):
        label = f"[{i+1}/{len(months)}] {y}-{m:02d}"
        t0 = time.time()
        try:
            calc.update(start_date=start, end_date=end)
            elapsed = time.time() - t0
            logger.info(f"{label} OK ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"{label} FAIL ({elapsed:.0f}s): {e}")
            failed.append(f"{y}-{m:02d}")
            continue

    total_elapsed = time.time() - t_total
    logger.info(f"=== 完成 === 共 {len(months)} 月, 失败 {len(failed)}, 总耗时 {total_elapsed:.0f}s")
    if failed:
        logger.warning(f"失败月份: {failed}")

    # 最终统计
    final_cnt = pd.read_sql("SELECT COUNT(*) AS c FROM panel_market_sentiment_monthly", engine).iloc[0, 0]
    rng = pd.read_sql(
        "SELECT MIN(trade_date), MAX(trade_date) FROM panel_market_sentiment_monthly", engine
    )
    logger.info(f"表最终状态: {final_cnt:,} 行, {rng.iloc[0,0]} ~ {rng.iloc[0,1]}")


if __name__ == "__main__":
    main()
