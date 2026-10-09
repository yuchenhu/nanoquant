"""数据同步（API 接入层，tushare → MySQL）。

用法（命令权威见 README.md）：
    # 开机一键增量（水位 → 今天）
    scripts\py.bat scripts\ingest.py

    # 区间回补（幂等可断点重跑；行情类默认按年分块，逐年落库+推水位）
    scripts\py.bat scripts\ingest.py --start 20100101 --end 20261004

    # 只跑 / 排除某些接口
    scripts\py.bat scripts\ingest.py --only index_daily,index_weight
    scripts\py.bat scripts\ingest.py --exclude weekly,monthly

    # 列出全部接口与策略
    scripts\py.bat scripts\ingest.py --list

机制（细则见 DEV_GUIDE.md「数据更新机制」章）：
    1. 每个接口二选一：full_refresh（清单类，truncate 全量重刷，无水位）
       或 水位线增量（etl_biz_date 表，biz_date 列 = trade_date/end_date/ex_date）。
    2. 幂等：行情/财务/分红均 overwrite 按分区先删后写（trade_date/end_date/ex_date），
       同区间重跑 N 次结果一致。
    3. 财务 by_period：水位在 end_date；增量自动重拉最近 4 个报告期覆盖财报修订。
    4. 分块：by_trade_date 且区间 > 1 年时按年分块，每块独立落库并推进水位
       —— 中断后重跑从水位续跑，内存不积攒。
    5. 依赖顺序：trade_cal 最先（刷新内存日历缓存）→ 清单 → 行情 → 财务 → 分红。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "sync.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("sync")

INTER_SLEEP = 1.0   # 接口间停顿，避免 tushare 限频


def classify(cls) -> str:
    from data.etl.base import (
        TushareByExDateCalculator,
        TushareByPeriodCalculator,
        TushareByTradeDateCalculator,
        TushareFullRefreshCalculator,
    )
    if issubclass(cls, TushareByPeriodCalculator):
        return "by_period"
    if issubclass(cls, TushareByExDateCalculator):
        return "by_ex_date"
    if issubclass(cls, TushareByTradeDateCalculator):
        return "by_trade_date"
    if issubclass(cls, TushareFullRefreshCalculator):
        return "full_refresh"
    return "unknown"


def year_chunks(start: str, end: str) -> List[Tuple[str, str]]:
    """区间 > 1 年时切成年块 [(ys, ye), ...]，幂等可断点。"""
    sy, ey = int(start[:4]), int(end[:4])
    if ey - sy < 1:
        return [(start, end)]
    chunks = []
    for y in range(sy, ey + 1):
        lo = max(start, f"{y}0101")
        hi = min(end, f"{y}1231")
        if lo <= hi:
            chunks.append((lo, hi))
    return chunks


def run_one(name: str, cls, start: Optional[str], end: Optional[str]) -> Tuple[int, str]:
    """跑单个接口。by_trade_date 长区间按年分块；其余一次调用。"""
    t0 = time.time()
    calc = cls()
    try:
        if start and end and classify(cls) == "by_trade_date":
            chunks = year_chunks(start, end)
            total = 0
            for lo, hi in chunks:
                df = calc.update(start_date=lo, end_date=hi)  # 每块落库+推水位
                total += 0 if df is None else len(df)
                logger.info(f"  [CHUNK] {name} [{lo}, {hi}] 完成，累计 {total} 行")
            rows = total
        else:
            df = calc.update(start_date=start, end_date=end)
            rows = 0 if df is None else len(df)
        logger.info(f"  [OK] {name:18s} {rows:>9d} 行  {time.time() - t0:6.1f}s")
        return rows, "ok"
    except Exception as e:
        logger.exception(f"  [FAIL] {name}: {e}")
        return 0, f"error:{e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="API 同步：无参=增量；--start/--end=回补")
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd），不传=增量")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd），默认今天")
    parser.add_argument("--only", type=str, help="只跑指定接口（逗号分隔）")
    parser.add_argument("--exclude", type=str, help="排除指定接口（逗号分隔）")
    parser.add_argument("--list", action="store_true", help="列出所有接口后退出")
    args = parser.parse_args()

    from data.etl.loader import CALCULATORS

    if args.list:
        print(f"{'接口':<20}{'策略':<16}{'表名'}")
        print("-" * 56)
        for name, cls in CALCULATORS.items():
            print(f"{name:<20}{classify(cls):<16}{cls.table_name}")
        return 0

    from core.dates import reload_trade_cal

    start, end = args.start, args.end or None
    only = set(args.only.split(",")) if args.only else None
    exclude = set(args.exclude.split(",")) if args.exclude else set()
    targets = {
        name: cls for name, cls in CALCULATORS.items()
        if (only is None or name in only) and name not in exclude
    }
    if not targets:
        logger.warning("没有匹配的接口")
        return 0

    mode = f"区间回补 [{start}, {end or '今天'}]" if start else "增量补齐（水位 → 今天）"
    logger.info("=" * 72)
    logger.info(f"API 同步 | {mode} | 接口数: {len(targets)}")
    logger.info("=" * 72)

    # Phase 顺序：trade_cal 最先并刷新日历缓存，然后清单 → 行情 → 财务 → 分红
    order = {"full_refresh": 1, "by_trade_date": 2, "by_period": 3, "by_ex_date": 4}
    phases: dict = {}
    for name, cls in targets.items():
        phases.setdefault(classify(cls), []).append((name, cls))

    summary = {}
    t_all = time.time()
    for ph in sorted(phases, key=lambda p: order.get(p, 9)):
        label = {"full_refresh": "清单/全量", "by_trade_date": "行情",
                 "by_period": "财务/披露", "by_ex_date": "分红"}[ph]
        group = sorted(phases[ph], key=lambda x: 0 if x[0] == "trade_cal" else 1)
        logger.info(f"--- {label}（{len(group)} 个）---")
        for name, cls in group:
            summary[name] = run_one(name, cls, start, end)
            if name == "trade_cal":
                try:
                    reload_trade_cal()
                    logger.info("  trade_cal 缓存已刷新")
                except Exception as e:
                    logger.warning(f"  reload_trade_cal 失败（不阻塞）: {e}")
            time.sleep(INTER_SLEEP)

    ok = sum(1 for _, st in summary.values() if st == "ok")
    err = len(summary) - ok
    logger.info("=" * 72)
    logger.info(f"同步完成: {ok} ok / {err} error / {len(summary)} total | 耗时 {(time.time() - t_all) / 60:.1f} 分钟")
    for name, (_, st) in summary.items():
        if st != "ok":
            logger.error(f"  失败: {name} | {st[:60]}")
    logger.info("=" * 72)
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
