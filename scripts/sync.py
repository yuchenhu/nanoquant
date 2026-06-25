"""数据同步脚本（手动执行，不配调度）。

为「本地环境、一两周开机一次」设计：开机跑一次，自动补齐离线期间所有缺失数据。
也支持手动指定区间/接口回补。

================================ 用法 ================================

【最常用】开机一键补齐（增量，从各表水位续跑到今天）：
    python scripts/sync.py
    # 一两周没开机？不传日期 = 从水位次日补到今天，自动补齐这段缺口。

【手动区间回补】指定 biz_date 区间（overwrite 幂等，可重复跑）：
    python scripts/sync.py --start 20200101 --end 20201231

【只刷某些接口】：
    python scripts/sync.py --only daily,moneyflow,adj_factor
    python scripts/sync.py --start 20200101 --end 20201231 --only income

【排除某些接口】：
    python scripts/sync.py --exclude weekly,monthly

【列出所有接口】：
    python scripts/sync.py --list

============================ 区间语义（重要）============================
各策略对 --start/--end 的解释不同（4 类）：
  - by_trade_date（行情）   : trade_date 区间，逐交易日拉
  - by_period（财务/披露）  : 区间内所有季度末报告期（无区间则重拉最近 4 期）
  - by_ex_date（分红）      : ex_date 区间，逐除权日拉
  - full_refresh（清单）    : 忽略区间，每次全量刷新

============================ 注意事项 ============================
1. 全新空库首次用：必须先 --start 回补全历史（无参增量对空库只拉今天）。
   之后日常用无参 catch-up 即可。
2. trade_cal 永远最先跑并刷新缓存（其他任务依赖交易日历）。
3. 按依赖顺序执行：trade_cal → 清单类 → 行情 → 财务 → 分红。
4. 全程 overwrite/水位幂等，跑断了重跑无副作用。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("sync")

INTER_SLEEP = 1.0


def classify(cls) -> str:
    """按继承的中间基类判断策略。"""
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


def run_one(
    name: str, cls, start: Optional[str], end: Optional[str]
) -> Tuple[str, int, str]:
    """跑单个接口的数据同步。这是整个补数机制的核心，分两种模式：

    ────────────────────────────────────────────────────────────────
    模式 A：增量补齐（start/end 都为 None）—— 日常开机就用这个
    ────────────────────────────────────────────────────────────────
    调 calc.update() 不传日期。BaseCalculator.update 内部会：
      1. 读这张表的「水位线」（etl_biz_date 表里记的，该表已入库数据的最大业务日期）
         例：daily 表水位 = 上次跑到的最后一个 trade_date，比如 20251201
      2. 把起点设成「水位次日」(20251202)，终点设成「今天」
      3. 只拉这段缺口的数据

    → 所以你一两周开机一次，水位停在两周前，update() 自动补这两周。
      不用你手动算"上次跑到哪、这次从哪开始"，水位线替你记着。

    ────────────────────────────────────────────────────────────────
    模式 B：区间回补（传了 start/end）—— 手动补历史/补特定区间用
    ────────────────────────────────────────────────────────────────
    调 calc.update(start_date=start, end_date=end)，强制拉这个区间，忽略水位。
    用于：① 首次给空库灌历史；② 怀疑某段数据有问题，重新覆盖。

    ────────────────────────────────────────────────────────────────
    关键：start/end 是「业务日期(biz_date)」区间，4 类策略各自解释：
    ────────────────────────────────────────────────────────────────
      - by_trade_date(行情)  : 当 trade_date 区间，逐交易日拉
      - by_period(财务/披露) : 当报告期区间，拆成区间内所有季度末(0331/0630/0930/1231)
      - by_ex_date(分红)     : 当 ex_date(除权日) 区间
      - full_refresh(清单)   : 忽略区间，每次全量 truncate 重刷

    ────────────────────────────────────────────────────────────────
    为什么重复跑不会出问题（幂等）：
    ────────────────────────────────────────────────────────────────
    所有表落库用 overwrite（先按分区键 DELETE 本批、再批量写）或 truncate(全量重写)。
    同一区间跑 N 次 = 删 N 次写 N 次，结果完全一致，不会重复堆数据。
    所以「跑到一半断电/报错，直接重跑」是安全的。
    """
    strat = classify(cls)              # 该接口属于哪类策略（决定区间语义）
    t0 = time.time()
    try:
        calc = cls()
        if start and end:
            # 模式 B：区间回补
            df = calc.update(start_date=start, end_date=end)
        else:
            # 模式 A：增量。不传日期 → update 内部从水位次日续跑到今天
            df = calc.update()
        rows = len(df) if df is not None else 0
        el = time.time() - t0
        logger.info(f"  [OK] {name:18s} {strat:14s} {rows:>8d} 行  {el:5.1f}s")
        return strat, rows, "ok"
    except Exception as e:
        # 单接口失败不中断整体（记录后继续下一个），便于断点重跑
        el = time.time() - t0
        logger.exception(f"  [FAIL] {name}: {e}")
        return strat, 0, f"error:{e}"


def list_calculators() -> None:
    from data.etl.loader import CALCULATORS
    print(f"{'接口':<20}{'策略':<16}{'表名'}")
    print("-" * 56)
    for name, cls in CALCULATORS.items():
        print(f"{name:<20}{classify(cls):<16}{cls.table_name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="数据同步：无参=增量补齐到今天；--start/--end=区间回补"
    )
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd），不传=增量")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd），默认今天")
    parser.add_argument("--only", type=str, help="只跑指定接口（逗号分隔）")
    parser.add_argument("--exclude", type=str, help="排除指定接口（逗号分隔）")
    parser.add_argument("--list", action="store_true", help="列出所有接口后退出")
    args = parser.parse_args()

    if args.list:
        list_calculators()
        return 0

    from core.dates import reload_trade_cal
    from data.etl.loader import CALCULATORS

    start = args.start
    end = args.end or None
    only = set(args.only.split(",")) if args.only else None
    exclude = set(args.exclude.split(",")) if args.exclude else set()

    # 筛选目标接口
    targets = {
        name: cls
        for name, cls in CALCULATORS.items()
        if (only is None or name in only) and name not in exclude
    }
    if not targets:
        logger.warning("没有匹配的接口")
        return 0

    mode = f"区间回补 [{start}, {end or '今天'}]" if start else "增量补齐（水位 → 今天）"
    logger.info("=" * 72)
    logger.info(f"数据同步 | 模式: {mode} | 接口数: {len(targets)}")
    logger.info(f"日志: {LOG_FILE}")
    logger.info("=" * 72)

    # ── 按「更新策略」把接口分成 4 组 ──
    # 之所以分组+排顺序，是因为表之间有依赖：交易日历必须先有，行情/财务才好算。
    # 同一组内的接口彼此独立，可任意顺序；组之间按下面 Phase 1→4 的顺序跑。
    groups: Dict[str, List[Tuple[str, type]]] = {
        "full_refresh": [], "by_trade_date": [], "by_period": [], "by_ex_date": [],
    }
    for name, cls in targets.items():
        groups.setdefault(classify(cls), []).append((name, cls))

    summary: Dict[str, Tuple[str, int, str]] = {}
    t_all = time.time()

    # ── Phase 1：清单/全量刷新（trade_cal 等）──
    # trade_cal(交易日历)必须最先跑：后面所有"按交易日取数"的逻辑都依赖它，
    # 且跑完要 reload_trade_cal() 刷新内存缓存，否则新日历当次不生效。
    # 其余清单类（stock_basic/index_basic/fund_basic...）也在这步全量刷新。
    fr = sorted(groups["full_refresh"], key=lambda x: 0 if x[0] == "trade_cal" else 1)
    if fr:
        logger.info(f"--- Phase 1/4 清单/全量刷新（{len(fr)} 个）---")
        for name, cls in fr:
            summary[name] = run_one(name, cls, start, end)
            if name == "trade_cal":
                try:
                    reload_trade_cal()   # 交易日历入库后刷新缓存，当次即可用
                    logger.info("  trade_cal 缓存已刷新")
                except Exception as e:
                    logger.warning(f"  reload_trade_cal 失败（不阻塞）: {e}")
            time.sleep(INTER_SLEEP)      # 接口间停顿，避免 tushare 限频

    # ── Phase 2：行情类（by_trade_date）──
    # daily/adj_factor/moneyflow/fund_daily/index_weight 等，逐交易日 overwrite。
    if groups["by_trade_date"]:
        logger.info(f"--- Phase 2/4 行情类（{len(groups['by_trade_date'])} 个）---")
        for name, cls in groups["by_trade_date"]:
            summary[name] = run_one(name, cls, start, end)
            time.sleep(INTER_SLEEP)

    # ── Phase 3：财务/披露（by_period）──
    # 财务三表 + disclosure_date，按报告期 overwrite。
    # 增量时内部会「重拉最近 4 个报告期」覆盖财报修订（财报会被多次修订重发）。
    if groups["by_period"]:
        logger.info(f"--- Phase 3/4 财务/披露（{len(groups['by_period'])} 个）---")
        for name, cls in groups["by_period"]:
            summary[name] = run_one(name, cls, start, end)
            time.sleep(INTER_SLEEP)

    # Phase 4: by_ex_date（分红）
    if groups["by_ex_date"]:
        logger.info(f"--- Phase 4/4 分红（{len(groups['by_ex_date'])} 个）---")
        for name, cls in groups["by_ex_date"]:
            summary[name] = run_one(name, cls, start, end)
            time.sleep(INTER_SLEEP)

    # 汇总
    total = time.time() - t_all
    ok = sum(1 for v in summary.values() if v[2] == "ok")
    err = len(summary) - ok
    logger.info("=" * 72)
    logger.info(f"同步完成: {ok} ok / {err} error / {len(summary)} total | "
                f"耗时 {total/60:.1f} 分钟")
    if err:
        for name, (s, r, st) in summary.items():
            if st != "ok":
                logger.error(f"  失败: {name} | {st[:60]}")
    logger.info("=" * 72)
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
