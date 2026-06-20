"""接入层 ETL 运行脚本（Step 8）。

功能：调用 data/etl/loader.py 中的 Calculator，从 tushare 拉数入库。

用法：
    # 全量跑所有 ETL（从水位次日续跑到今天）
    python scripts/run_ingest.py

    # 指定 biz_date 区间回补
    python scripts/run_ingest.py --start 20240101 --end 20240131

    # 只跑指定接口
    python scripts/run_ingest.py --only daily,daily_basic

    # 排除某些接口
    python scripts/run_ingest.py --exclude income,balancesheet

    # 列出所有可用接口
    python scripts/run_ingest.py --list
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_ingest")


def list_calculators() -> None:
    """列出所有可用 ETL 接口。"""
    from data.etl.loader import CALCULATORS
    print("=" * 60)
    print(f"可用 ETL 接口（共 {len(CALCULATORS)} 个）")
    print("=" * 60)
    for name, cls in CALCULATORS.items():
        print(f"  {name:25s} → {cls.table_name}")
    print("=" * 60)


def run_ingest(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    only: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> int:
    """运行 ETL 接入。"""
    from data.etl.loader import CALCULATORS

    # 筛选要跑的接口
    targets = []
    for name, cls in CALCULATORS.items():
        if only and name not in only:
            continue
        if exclude and name in exclude:
            continue
        targets.append((name, cls))

    if not targets:
        logger.warning("没有匹配的接口可运行")
        return 0

    logger.info(f"将运行 {len(targets)} 个 ETL 接口")
    if start_date or end_date:
        logger.info(f"biz_date 区间: [{start_date or '水位次日'}, {end_date or '今天'}]")
    else:
        logger.info("biz_date 区间: 从水位次日续跑（增量）")

    success = 0
    failed = 0
    for name, cls in targets:
        try:
            logger.info(f"--- 开始: {name} → {cls.table_name} ---")
            instance = cls()
            result = instance.update(start_date=start_date, end_date=end_date)
            rows = len(result) if result is not None else 0
            logger.info(f"--- 完成: {name} ({rows} 行) ---")
            success += 1
        except Exception as e:
            logger.error(f"--- 失败: {name}: {e} ---", exc_info=True)
            failed += 1

    logger.info("=" * 60)
    logger.info(f"ETL 完成：成功 {success}，失败 {failed}")
    logger.info("=" * 60)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 ETL 接入层（tushare → DB）")
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd），不传则从水位续跑")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd），不传则到今天")
    parser.add_argument("--only", type=str, help="只跑指定接口（逗号分隔，如 daily,daily_basic）")
    parser.add_argument("--exclude", type=str, help="排除指定接口（逗号分隔）")
    parser.add_argument("--list", action="store_true", help="列出所有可用接口后退出")
    args = parser.parse_args()

    if args.list:
        list_calculators()
        return 0

    only = args.only.split(",") if args.only else None
    exclude = args.exclude.split(",") if args.exclude else None

    return run_ingest(
        start_date=args.start,
        end_date=args.end,
        only=only,
        exclude=exclude,
    )


if __name__ == "__main__":
    sys.exit(main())
