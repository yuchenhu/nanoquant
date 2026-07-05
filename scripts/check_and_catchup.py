"""补齐三张表：index_membership_monthly → panel_stock_daily → market_sentiment_monthly。

先检查上游数据，缺则补则跳过有问题的表。
用 sync.py + run_compute.py 的底层逻辑，按顺序执行。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import argparse
import logging
import time
from datetime import datetime, timedelta

import pandas as pd
from config.database import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "logs" / "catchup.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("catchup")


# ============================================================
# 阶段 0：检查上游数据
# ============================================================
def check_upstream():
    """检查 panel_stock_daily 和 market_sentiment_monthly 所依赖的上游表。"""
    upstream = {
        "stock_daily":       "panel_stock_daily 的核心输入",
        "adj_factor":        "panel_stock_daily join 复权",
        "stock_daily_basic": "panel_stock_daily join PE/PB/市值",
        "stock_st":          "panel_stock_daily join ST 状态",
        "stock_basic":       "panel_stock_daily join 股票基本信息",
        "index_member_all":  "panel_stock_daily join 申万行业",
        "suspend":           "panel_stock_daily join 停牌",
        "index_weight":      "index_membership_monthly 的输入",
        "index_daily":       "market_sentiment_monthly join 指数行情",
        "moneyflow_hsgt":    "market_sentiment_monthly join 北向",
        "margin":            "market_sentiment_monthly join 两融",
        "limit_list_d":      "market_sentiment_monthly join 涨跌停",
        "moneyflow":         "market_sentiment_monthly join 资金流",
    }

    ok = []
    empty = []
    missing = []
    for tbl, desc in upstream.items():
        try:
            cnt = pd.read_sql(f"SELECT COUNT(*) FROM `{tbl}`", engine).iloc[0, 0]
            if cnt > 0:
                ok.append((tbl, cnt, desc))
            else:
                empty.append((tbl, desc))
        except Exception as e:
            missing.append((tbl, str(e)[:60], desc))

    logger.info("=" * 60)
    logger.info("上游数据检查")
    logger.info("=" * 60)
    for t, cnt, desc in ok:
        logger.info(f"  [OK]    {t:30s} {cnt:>10,}行  ({desc})")
    for t, desc in empty:
        logger.warning(f"  [EMPTY] {t:30s} 0行               ({desc})")
    for t, err, desc in missing:
        logger.error(f"  [MISS]  {t:30s} 表不存在         ({desc})")

    return ok, empty, missing


# ============================================================
# 阶段 1：index_weight 增量补齐 → index_membership_monthly
# ============================================================
def fill_index_membership():
    """先增量补齐 index_weight，再全量算 index_membership_monthly。"""
    logger.info("=" * 60)
    logger.info("阶段 1：index_membership_monthly")
    logger.info("=" * 60)

    # 1a. 检查 index_weight 范围
    try:
        rng = pd.read_sql("SELECT MIN(trade_date), MAX(trade_date) FROM index_weight", engine)
        mi, ma = rng.iloc[0, 0], rng.iloc[0, 1]
        logger.info(f"index_weight 现有范围: {mi} ~ {ma}")
    except Exception:
        logger.error("index_weight 表不存在，需先跑 sync.py")
        return False

    # 1b. 全量计算 index_membership_monthly
    from data.panel.index_membership_monthly import IndexMembershipMonthlyCalculator

    calc = IndexMembershipMonthlyCalculator(engine=engine)
    logger.info("计算 index_membership_monthly（全量 2010-01 ~ 今）...")
    t0 = time.time()
    try:
        calc.update(start_date="20100101", end_date="20260630")
        elapsed = time.time() - t0
        logger.info(f"index_membership_monthly 完成 ({elapsed:.0f}s)")

        # 验证
        cnt = pd.read_sql("SELECT COUNT(*) FROM panel_index_membership_monthly", engine).iloc[0, 0]
        logger.info(f"  => {cnt:,} 行")
        return cnt > 0
    except Exception as e:
        logger.error(f"index_membership_monthly 失败: {e}")
        return False


# ============================================================
# 阶段 2：panel_stock_daily（逐年回补）
# ============================================================
def fill_stock_daily_panel():
    """逐年回补 panel_stock_daily（index_membership_monthly 就绪后跑）。"""
    logger.info("=" * 60)
    logger.info("阶段 2：panel_stock_daily")
    logger.info("=" * 60)

    from data.panel.stock_daily_panel import StockDailyPanelCalculator
    calc = StockDailyPanelCalculator(engine=engine)

    # 先跑 1 个月验证
    logger.info("先跑 2024-01 验证...")
    t0 = time.time()
    try:
        calc.update(start_date="20240101", end_date="20240131")
        elapsed = time.time() - t0
        cnt = pd.read_sql(
            "SELECT COUNT(*) FROM panel_stock_daily WHERE trade_date BETWEEN '2024-01-01' AND '2024-01-31'",
            engine
        ).iloc[0, 0]
        logger.info(f"2024-01 验证通过: {cnt:,} 行 ({elapsed:.0f}s)")

        # 检查 is_hs300 列是否全为 0
        has_index = pd.read_sql(
            "SELECT SUM(is_hs300) FROM panel_stock_daily WHERE trade_date BETWEEN '2024-01-01' AND '2024-01-31'",
            engine
        ).iloc[0, 0]
        if has_index == 0:
            logger.warning("警告: is_hs300 全为 0！index_membership_monthly 可能仍为空")
            return False
    except Exception as e:
        logger.error(f"2024-01 验证失败: {e}")
        return False

    # 逐年回补（2010-2026）
    from datetime import datetime
    years_failed = []
    current_year = datetime.now().year
    for y in range(2010, current_year + 1):
        start = f"{y}0101"
        end = f"{y}1231"
        logger.info(f"--- {y} ({start}~{end}) ---")
        t0 = time.time()
        try:
            calc.update(start_date=start, end_date=end)
            elapsed = time.time() - t0
            logger.info(f"  {y} OK ({elapsed:.0f}s)")
        except Exception as e:
            logger.error(f"  {y} FAIL: {e}")
            years_failed.append(y)

    if years_failed:
        logger.warning(f"失败年份: {years_failed}")
        return False

    # 最终验证
    cnt = pd.read_sql("SELECT COUNT(*) FROM panel_stock_daily", engine).iloc[0, 0]
    rng = pd.read_sql("SELECT MIN(trade_date), MAX(trade_date) FROM panel_stock_daily", engine)
    logger.info(f"panel_stock_daily 完成: {cnt:,} 行, {rng.iloc[0, 0]} ~ {rng.iloc[0, 1]}")
    return cnt > 0


# ============================================================
# 阶段 3：market_sentiment_monthly
# ============================================================
def fill_market_sentiment():
    """用 backfill 脚本逐月回补 market_sentiment_monthly。"""
    logger.info("=" * 60)
    logger.info("阶段 3：market_sentiment_monthly")
    logger.info("=" * 60)

    from data.panel.market_sentiment_monthly import MarketSentimentMonthlyCalculator
    calc = MarketSentimentMonthlyCalculator(engine=engine)

    # 先跑 1 个月验证
    logger.info("先跑 2024-06 验证...")
    t0 = time.time()
    try:
        calc.update(start_date="20240601", end_date="20240630")
        elapsed = time.time() - t0
        cnt = pd.read_sql(
            "SELECT COUNT(*) FROM panel_market_sentiment_monthly WHERE trade_date='2024-06-28'",
            engine
        ).iloc[0, 0]
        logger.info(f"2024-06 验证通过: {cnt} 行 ({elapsed:.0f}s)")
    except Exception as e:
        logger.error(f"2024-06 验证失败: {e}")
        return False

    # 逐月回补 2010-01 ~ 2026-06
    from calendar import monthrange
    months = []
    for y in range(2010, 2027):
        end_m = 12 if y < 2026 else 6
        for m in range(1, end_m + 1):
            last_day = monthrange(y, m)[1]
            start = f"{y}{m:02d}01"
            end = f"{y}{m:02d}{last_day:02d}"
            months.append((y, m, start, end))

    failed = []
    for y, m, start, end in months:
        logger.info(f"--- {y}-{m:02d} ({start}~{end}) ---")
        t0 = time.time()
        try:
            calc.update(start_date=start, end_date=end)
            elapsed = time.time() - t0
            logger.info(f"  OK ({elapsed:.0f}s)")
        except Exception as e:
            logger.error(f"  FAIL: {e}")
            failed.append(f"{y}-{m:02d}")

    if failed:
        logger.warning(f"失败月份: {failed}")
        return False

    cnt = pd.read_sql("SELECT COUNT(*) FROM panel_market_sentiment_monthly", engine).iloc[0, 0]
    rng = pd.read_sql(
        "SELECT MIN(trade_date), MAX(trade_date) FROM panel_market_sentiment_monthly", engine
    )
    logger.info(f"market_sentiment_monthly 完成: {cnt:,} 行, {rng.iloc[0,0]} ~ {rng.iloc[0,1]}")
    return cnt > 0


# ============================================================
# main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="三步补齐：index_membership → panel_stock_daily → market_sentiment")
    parser.add_argument("--check-only", action="store_true", help="只检查上游数据，不跑计算")
    parser.add_argument("--step", type=int, choices=[1, 2, 3], help="只跑指定步骤")
    parser.add_argument("--test", action="store_true", help="测试模式：每步只跑 1 个月验证")
    args = parser.parse_args()

    # Step 0: 检查上游
    ok, empty, missing = check_upstream()

    if args.check_only:
        return

    if empty or missing:
        logger.warning("存在空表/缺表，请先用 sync.py 补齐上游数据")
        for t, desc in empty:
            logger.warning(f"  需补: {t} ({desc})")
        for t, _, desc in missing:
            logger.warning(f"  需建/补: {t} ({desc})")
        logger.info("示例: python scripts/sync.py --start 20100101 --end 20260630 --only daily,adj_factor,...")
        return

    if args.step and args.step > 1:
        pass
    else:
        # Step 1
        if not fill_index_membership():
            logger.error("中断: index_membership_monthly 失败")
            return

    if args.test:
        logger.info("=== 测试模式结束 ===")
        return

    if args.step and args.step != 2:
        pass
    else:
        # Step 2
        if not fill_stock_daily_panel():
            logger.error("中断: panel_stock_daily 失败")
            return

    if args.step and args.step != 3:
        pass
    else:
        # Step 3
        if not fill_market_sentiment():
            logger.error("中断: market_sentiment_monthly 失败")
            return

    logger.info("=" * 60)
    logger.info("全部完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
