"""逐年回补脚本：从指定起始年补到今天，每年一个断点（可中断续跑）。

为「手动分批补历史」设计：你已补到某年，用这个一口气补到今天，
每补完一年记一个断点；中途断了重跑会自动跳过已完成的年份。

────────────────────────────── 用法 ──────────────────────────────
    # 从 2012 年补到今天（默认）
    python scripts/backfill_years.py

    # 指定起始年（比如你已补到 2011 年底，从 2012 开始）
    python scripts/backfill_years.py --from-year 2012

    # 指定起止年
    python scripts/backfill_years.py --from-year 2012 --to-year 2026

    # 重置进度，全部重补
    python scripts/backfill_years.py --from-year 2012 --reset

────────────────────────────── 机制 ──────────────────────────────
1. 先把「清单类(full_refresh)」全量刷一次（trade_cal 最先 + reload 缓存）。
   这类无视年份，跑一次拿当前全量快照即可。
2. 然后逐年回补「行情 / 财务 / 分红」三类（exclude 清单类）：
   - 每年 = sync 的区间回补 [YYYY0101, YYYY1231]（当年是 [0101, today]）
   - 一年跑完写进度文件 logs/backfill_progress.txt
   - 重跑时读进度，已完成的年份直接跳过（断点续跑）
3. 全程 overwrite/水位幂等，断电/报错直接重跑无副作用。

日志：logs/backfill_years.log（含每年每接口的行数）
进度：logs/backfill_progress.txt（每行一个已完成的年份）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Set

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "backfill_years.log"
PROGRESS_FILE = LOG_DIR / "backfill_progress.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backfill_years")

# 清单类（full_refresh）：无视年份，单独刷一次
REFRESH_KEYS = [
    "trade_cal", "stock_basic", "index_basic",
    "index_classify", "index_member_all", "fund_basic",
]
INTER_SLEEP = 1.0


def load_progress() -> Set[int]:
    """读已完成年份。"""
    if not PROGRESS_FILE.exists():
        return set()
    done = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            done.add(int(line))
    return done


def mark_done(year: int) -> None:
    """记录某年已完成。"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{year}\n")


def refresh_lists() -> None:
    """清单类全量刷一次（trade_cal 最先 + reload 缓存）。"""
    from core.dates import reload_trade_cal
    from data.etl.loader import CALCULATORS
    from scripts.sync import run_one

    logger.info("=== 清单类全量刷新（full_refresh）===")
    for name in REFRESH_KEYS:
        cls = CALCULATORS.get(name)
        if not cls:
            continue
        run_one(name, cls, None, None)  # full_refresh 忽略区间
        if name == "trade_cal":
            try:
                reload_trade_cal()
                logger.info("  trade_cal 缓存已刷新")
            except Exception as e:
                logger.warning(f"  reload_trade_cal 失败（不阻塞）: {e}")
        time.sleep(INTER_SLEEP)


def backfill_one_year(year: int, today: str) -> bool:
    """回补某一年的行情/财务/分红（exclude 清单类）。返回是否全成功。"""
    from data.etl.loader import CALCULATORS
    from scripts.sync import classify, run_one

    start = f"{year}0101"
    end = f"{year}1231" if f"{year}1231" <= today else today
    logger.info(f"────── 回补 {year} 年 [{start}, {end}] ──────")

    # 按策略分组（exclude 清单类），按 Phase 顺序：行情 → 财务 → 分红
    order = {"by_trade_date": 1, "by_period": 2, "by_ex_date": 3}
    targets = [
        (name, cls)
        for name, cls in CALCULATORS.items()
        if name not in REFRESH_KEYS and classify(cls) in order
    ]
    targets.sort(key=lambda x: order[classify(x[1])])

    all_ok = True
    for name, cls in targets:
        _, _, status = run_one(name, cls, start, end)
        if status != "ok":
            all_ok = False
        time.sleep(INTER_SLEEP)
    return all_ok


def main() -> int:
    from core.dates import get_today_str

    parser = argparse.ArgumentParser(description="逐年回补历史数据（每年一个断点）")
    parser.add_argument("--from-year", type=int, default=2012, help="起始年（默认2012）")
    parser.add_argument("--to-year", type=int, default=None, help="结束年（默认今年）")
    parser.add_argument("--reset", action="store_true", help="重置进度，全部重补")
    args = parser.parse_args()

    today = get_today_str()
    to_year = args.to_year or int(today[:4])
    from_year = args.from_year

    if args.reset and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        logger.info("已重置进度文件")

    done = load_progress()
    years: List[int] = [y for y in range(from_year, to_year + 1) if y not in done]

    logger.info("=" * 72)
    logger.info(f"逐年回补 | {from_year}~{to_year} | 今天={today}")
    logger.info(f"已完成(跳过): {sorted(done)}")
    logger.info(f"待补年份: {years}")
    logger.info("=" * 72)

    # Step 1: 清单类全量刷一次
    refresh_lists()

    # Step 2: 逐年回补
    t_all = time.time()
    for year in years:
        t0 = time.time()
        ok = backfill_one_year(year, today)
        el = (time.time() - t0) / 60
        if ok:
            mark_done(year)
            logger.info(f"✓ {year} 年完成（{el:.1f} 分钟），已记断点")
        else:
            logger.error(
                f"✗ {year} 年有接口失败（{el:.1f} 分钟），未记断点。"
                f"修复后重跑会从本年继续。"
            )
            # 不中断，继续下一年（失败的年份下次重跑会重补）

    logger.info("=" * 72)
    logger.info(f"全部完成，总耗时 {(time.time()-t_all)/60:.1f} 分钟")
    logger.info(f"进度文件: {PROGRESS_FILE}")
    logger.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
