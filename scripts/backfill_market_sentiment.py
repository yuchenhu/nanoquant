"""市场情绪底表回补脚本（按年批量拉数，I/O 优化版）。

设计逻辑：
- 按年分组：同一年的月份合并为一次 calc.update()，panel_stock_daily 只读一次，
  避免逐月重复读取相同数据（IO 从 192 次降到 ~16 次/16年）
- 断点粒度：年。--resume 跳过已完成年份的全部月份
- 每个自然年独立计算，失败不污染其他年

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
write_mode：overwrite + partition_col=trade_date（按月独立覆盖）

用法：
    # 先验证12个月（推荐）
    python scripts/backfill_market_sentiment.py --start 20240101 --end 20241231

    # 全量回补
    python scripts/backfill_market_sentiment.py --start 20100101 --end 20260630

    # 只跑一个月（跨年边界自动收窄到该月所在年）
    python scripts/backfill_market_sentiment.py --start 20240501 --end 20240531

    # 断点续跑（读完已跑年份自动跳过）
    python scripts/backfill_market_sentiment.py --start 20100101 --end 20260630 --resume

机制：
- 月份按自然年分组，如 2024 年 12 个月 → 一次 calc.update("20240101", "20241231")
- get_data 一次读取 1 年前溯日线 + 5 年前溯月末抽样，全年月份共享同一块数据
- process_data 内循环所有月份，逐月计算
- 每完成一个年份，记录该年全部月份到断点文件
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "backfill_market_sentiment.log"
PROGRESS_FILE = LOG_DIR / "backfill_market_sentiment_progress.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backfill_msm")


def get_month_ends(start_date: str, end_date: str) -> list[str]:
    """区间内每月最后交易日（yyyymmdd 格式），从 trade_cal 查。"""
    from config.database import engine
    from core.dates import reload_trade_cal, get_monthly_last_tradedate

    reload_trade_cal()
    sy, ey = int(start_date[:4]), int(end_date[:4])
    result = []
    for d in get_monthly_last_tradedate(engine, sy, ey):
        if start_date[:6] <= d[:6] <= end_date[:6]:
            result.append(d)  # yyyymmdd
    return result


def load_progress() -> Set[str]:
    """读已完成月份（yyyymm）。兼容旧格式。"""
    if not PROGRESS_FILE.exists():
        return set()
    done = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if len(line) == 6 and line.isdigit():
            done.add(line)
    return done


def mark_year_done(year_months: List[str]) -> None:
    """记录一个年份的全部月份已完成。"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        for ym in year_months:
            f.write(f"{ym}\n")


def group_by_year(month_ends: list[str]) -> Dict[str, List[str]]:
    """按月分组为 {yyyy: [yyyymmdd, ...]}。"""
    groups: Dict[str, List[str]] = defaultdict(list)
    for d in month_ends:
        groups[d[:4]].append(d)
    return dict(groups)


def run_year_batch(first_date: str, last_date: str, year_months: list[str]) -> int:
    """对一个自然年（或部分年）的所有月份，一次 calc.update() 完成。

    返回落库行数。
    """
    from data.panel.market_sentiment_monthly import (
        MarketSentimentMonthlyCalculator,
    )

    calc = MarketSentimentMonthlyCalculator()
    n_months = len(year_months)
    logger.info(
        "  调用 calc.update(%s, %s) → %d 个月",
        first_date, last_date, n_months,
    )
    result = calc.update(start_date=first_date, end_date=last_date)
    return len(result) if result is not None else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按年批量回补市场情绪底表")
    parser.add_argument("--start", type=str, required=True,
                        help="起始日期 yyyymmdd（如 20240101）")
    parser.add_argument("--end", type=str, required=True,
                        help="结束日期 yyyymmdd（如 20241231）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续跑：跳过已完成的年份")
    args = parser.parse_args()

    # 获取区间内所有月末交易日
    month_ends = get_month_ends(args.start, args.end)
    if not month_ends:
        logger.error(f"[{args.start}, {args.end}] 区间内无月末交易日，检查 trade_cal")
        return 1

    # 按年分组
    year_groups = group_by_year(month_ends)

    # 断点续跑：过滤已完成年份
    done_ym = load_progress() if args.resume else set()
    pending_groups: Dict[str, List[str]] = {}
    skipped_years = 0
    for year, months in year_groups.items():
        # 该年所有月份都已完成 → 跳过
        if all(m[:6] in done_ym for m in months):
            skipped_years += 1
            continue
        pending_groups[year] = months

    total_years = len(year_groups)
    total_months = len(month_ends)
    pending_months = sum(len(v) for v in pending_groups.values())

    logger.info("=" * 60)
    logger.info("按年批量回补 market_sentiment_monthly")
    logger.info("区间: [%s, %s] → %d 个月 (%d 年)",
                args.start, args.end, total_months, total_years)
    if args.resume and skipped_years:
        logger.info("断点续跑: 已跳过 %d 年，待跑 %d 年 (%d 个月)",
                    skipped_years, len(pending_groups), pending_months)
    logger.info("=" * 60)

    if not pending_groups:
        logger.info("所有年份已完成，无需重跑")
        return 0

    t_all = time.time()
    success_years = 0
    failed_years = 0

    for i, (year, months) in enumerate(sorted(pending_groups.items())):
        first_date = months[0]
        last_date = months[-1]
        year_months_yyyymm = [m[:6] for m in months]

        logger.info("[%d/%d] 批量计算 %s 年 (%d 个月): %s → %s",
                    i + 1, len(pending_groups), year, len(months),
                    first_date, last_date)
        t0 = time.time()
        try:
            rows = run_year_batch(first_date, last_date, months)
            el = time.time() - t0
            logger.info("  完成: %d 行, 耗时 %.1fs (%.1f 秒/月)",
                        rows, el, el / len(months))
            mark_year_done(year_months_yyyymm)
            success_years += 1
        except Exception as e:
            el = time.time() - t0
            logger.error("  失败: %s (耗时 %.1fs)", e, el, exc_info=True)
            failed_years += 1
            logger.warning("  未记断点，修复后 --resume 会重跑 %s 年", year)

    total_el = (time.time() - t_all) / 60
    logger.info("=" * 60)
    logger.info("完成: 成功 %d/%d 年, 失败 %d, 总耗时 %.1f 分钟",
                success_years, len(pending_groups), failed_years, total_el)
    logger.info("进度文件: %s", PROGRESS_FILE)
    logger.info("=" * 60)
    return 0 if failed_years == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
