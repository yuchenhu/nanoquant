"""月度市场情绪底表逐月回补脚本。

设计逻辑：
- 历史回补：每月最后交易日跑一次，产出当月完整快照
- 日常增量（待调度层实现）：每日跑 MTD 覆盖当月行，月末自动收敛为"最终版"

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
write_mode：overwrite + partition_col=trade_date（按月独立写，互不覆盖）

用法：
    # 先验证12个月（推荐）
    python scripts/backfill_market_sentiment.py --start 20240101 --end 20241231

    # 验证通过后全量回补
    python scripts/backfill_market_sentiment.py --start 20100101 --end 20260630

    # 只跑一个月测试
    python scripts/backfill_market_sentiment.py --start 20240501 --end 20240531

    # 断点续跑（读完已跑月份自动跳过）
    python scripts/backfill_market_sentiment.py --start 20100101 --end 20260630 --resume

机制：
- 每次跑一个交易日（1个月末），跑完即记断点
- 支持 --resume：读完进度跳过已跑月份
- 每个月的 psd 日线只拉最近1年（+5年PE月末抽样），内存安全
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Set

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
    """读已完成月份（yyyymm）。"""
    if not PROGRESS_FILE.exists():
        return set()
    done = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if len(line) == 6 and line.isdigit():
            done.add(line)
    return done


def mark_done(yyyymm: str) -> None:
    """记录某月已完成。"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{yyyymm}\n")


def run_one_month(trade_date: str) -> int:
    """用单个月末交易日跑一次 Calculator.update()。返回行数。"""
    from data.panel.market_sentiment_monthly import (
        MarketSentimentMonthlyCalculator,
    )

    calc = MarketSentimentMonthlyCalculator()
    result = calc.update(start_date=trade_date, end_date=trade_date)
    return len(result) if result is not None else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="逐月回补市场情绪底表")
    parser.add_argument("--start", type=str, required=True,
                        help="起始日期 yyyymmdd（如 20240101）")
    parser.add_argument("--end", type=str, required=True,
                        help="结束日期 yyyymmdd（如 20241231）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续跑：跳过已完成的月份")
    args = parser.parse_args()

    # 获取区间内所有月末交易日
    month_ends = get_month_ends(args.start, args.end)
    if not month_ends:
        logger.error(f"[{args.start}, {args.end}] 区间内无月末交易日，检查 trade_cal")
        return 1

    # 断点续跑：过滤已完成月份
    done_ym = load_progress() if args.resume else set()
    pending = [d for d in month_ends if d[:6] not in done_ym]

    logger.info("=" * 60)
    logger.info(f"逐月回补 market_sentiment_monthly")
    logger.info(f"区间: [{args.start}, {args.end}] → {len(month_ends)} 个月末交易日")
    if args.resume and done_ym:
        logger.info(f"断点续跑: 已跳过 {len(done_ym)} 个月，待跑 {len(pending)} 个月")
    logger.info("=" * 60)

    if not pending:
        logger.info("所有月份已完成，无需重跑")
        return 0

    t_all = time.time()
    success = 0
    failed = 0

    for i, dt in enumerate(pending):
        yyyymm = dt[:6]
        logger.info(f"[{i+1}/{len(pending)}] 计算 {yyyymm} (date={dt}) ...")
        t0 = time.time()
        try:
            rows = run_one_month(dt)
            el = time.time() - t0
            logger.info(f"  完成: {rows} 行, 耗时 {el:.1f}s")
            mark_done(yyyymm)
            success += 1
        except Exception as e:
            el = time.time() - t0
            logger.error(f"  失败: {e} (耗时 {el:.1f}s)", exc_info=True)
            failed += 1
            logger.warning(f"  未记断点，修复后 --resume 会重跑 {yyyymm}")

    total_el = (time.time() - t_all) / 60
    logger.info("=" * 60)
    logger.info(f"完成: 成功 {success}/{len(pending)}, 失败 {failed}, 总耗时 {total_el:.1f} 分钟")
    logger.info(f"进度文件: {PROGRESS_FILE}")
    logger.info("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
