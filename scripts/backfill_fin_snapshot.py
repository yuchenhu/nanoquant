# -*- coding: utf-8 -*-
"""财务快照月频回补（panel_financial_statements_snapshot）。

前置链条（必须先就绪）：
    sync: daily_basic（etl） -> compute: panel_mv_monthly -> 本脚本
    （财务三表 / disclosure_date / dividend 由 sync 的 income/balancesheet/cashflow/
      disclosure_date/dividend 接口回补）

用法：
    # 体检前置覆盖 + 列出计划快照，不写库：
    python scripts/backfill_fin_snapshot.py --start 20100630 --end 20260930 --dry-run

    # 全量回补（逐月落库、断点可重跑，默认跳过已有快照月）：
    python scripts/backfill_fin_snapshot.py --start 20100630 --end 20260930

    # 增量（无区间 = 水位续跑到今天，只补月末）：
    python scripts/backfill_fin_snapshot.py

    # 重刷某些月（忽略已有）：
    python scripts/backfill_fin_snapshot.py --start 20240101 --end 20241231 --force

说明：
- snapshot_date = 每月最后交易日（月频固化在计算器 update 内，sync 全量时段也只按月末）
- lookback 默认 2 年（8 份/股）；改深度需全量重刷
- dividend 本地仅 2015-05 起：更早月份的分红列为空，不影响 PE/PB，影响股息率类因子
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "backfill_fin_snapshot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("backfill_fin_snapshot")

# 前置表：(表名, 覆盖检查用的日期列)
PREREQ = [
    ("disclosure_date", "end_date"),
    ("income", "end_date"),
    ("balancesheet", "end_date"),
    ("cashflow", "end_date"),
    ("panel_mv_monthly", "trade_date"),
    ("dividend", "end_date"),
]


def check_prereq() -> bool:
    """前置体检：任一核心表为空则中止（dividend 缺历史仅告警）。"""
    ok = True
    logger.info("=" * 64)
    logger.info("前置体检")
    for table, col in PREREQ:
        try:
            from config.database import execute_sql

            d = execute_sql(f"SELECT COUNT(*) AS n, MIN({col}) AS d0, MAX({col}) AS d1 FROM {table}")
            n, d0, d1 = int(d.iloc[0]["n"]), d.iloc[0]["d0"], d.iloc[0]["d1"]
        except Exception as e:
            logger.error("  [ERR] %s 查询失败: %s", table, e)
            ok = False
            continue
        if n == 0:
            if table == "dividend":
                logger.warning("  [WARN] %s 为空：分红列全空，PE/PB 不受影响", table)
            else:
                logger.error("  [EMPTY] %s 为空，中止（先跑上游 sync/compute）", table)
                ok = False
        else:
            logger.info("  [OK] %-20s rows=%-9d %s -> %s", table, n, d0, d1)
    logger.info("=" * 64)
    return ok


def existing_snapshots() -> set:
    from config.database import execute_sql

    d = execute_sql("SELECT DISTINCT snapshot_date FROM panel_financial_statements_snapshot")
    return set(str(x).replace("-", "") for x in d["snapshot_date"])


# ---------------------------------------------------------------------------
# fast 批量路径（2026-10-04）：一次性预载六源 + 逐月内存 PIT 过滤
# ---------------------------------------------------------------------------
# 原路径（calc.update 逐月）每月对六张表各做一次全表扫描（WHERE end_date BETWEEN
# 命中不了复合主键前导列），约 40s/月 x 195 月。fast 路径六表各扫一次，
# 逐月只做布尔过滤。PIT 条件与 get_data 的 SQL 逐条等价，见 _pit_subsets 注释。


def _preload_sources(calc, first_snap: str, last_snap: str) -> dict:
    """一次性读入六源整段 [first_snap - lookback, last_snap]。

    条件只含 end_date 范围（与 PIT 无关的部分）；逐月 PIT 条件在
    _pit_subsets 里做。dividend 额外带出 update_flag 供组内取最新版。
    """
    import pandas as pd
    from config.database import execute_sql

    lo = (
        pd.to_datetime(first_snap, format="%Y%m%d") - pd.DateOffset(years=calc.lookback_years)
    ).strftime("%Y%m%d")
    hi = last_snap
    logger.info("fast 预载六源 [%s, %s]", lo, hi)
    src = {}
    src["disclosure_date"] = execute_sql(
        f"SELECT * FROM disclosure_date WHERE end_date >= '{lo}' AND end_date <= '{hi}'"
    )
    for t in ("balancesheet", "income", "cashflow"):
        src[t] = execute_sql(
            f"SELECT * FROM {t} WHERE end_date >= '{lo}' AND end_date <= '{hi}'"
        )
    src["dividend"] = execute_sql(
        "SELECT ts_code, end_date, ex_date, cash_div, base_share, update_flag FROM dividend "
        f"WHERE end_date >= '{lo}' AND end_date <= '{hi}' AND div_proc='实施' AND cash_div > 0"
    )
    src["mv"] = execute_sql(
        f"SELECT * FROM panel_mv_monthly WHERE trade_date >= '{lo}' AND trade_date <= '{hi}'"
    )
    for k, v in src.items():
        logger.info("  预载 %-16s %d 行", k, len(v))
    return src


def _pit_subsets(src: dict, calc, snap: str) -> dict:
    """逐月 PIT 过滤：与 get_data(snap, snap) 的 SQL WHERE 逐条等价。

    NaT/NULL 比较在 SQL 里不成立（行被排除），pandas NaT <= s 也为 False，等价。
    """
    import pandas as pd

    s = pd.Timestamp(pd.to_datetime(snap, format="%Y%m%d"))
    lo = s - pd.DateOffset(years=calc.lookback_years)
    out = {}

    # disclosure_date: end_date in [lo,s] AND (actual_date<=s OR modify_date<=s)
    d = src["disclosure_date"]
    ed, ac, mo = (pd.to_datetime(d[c]) for c in ("end_date", "actual_date", "modify_date"))
    out["disclosure_date"] = d.loc[(ed >= lo) & (ed <= s) & ((ac <= s) | (mo <= s))]

    # 三表: end_date in [lo,s] AND f_ann_date <= s
    for t in ("balancesheet", "income", "cashflow"):
        x = src[t]
        ed = pd.to_datetime(x["end_date"])
        fa = pd.to_datetime(x["f_ann_date"])
        out[t] = x.loc[(ed >= lo) & (ed <= s) & (fa <= s)]

    # dividend: end_date in [lo,s] AND ex_date<=s（div_proc/现金已在预载过滤），
    # 组内取 update_flag 最大一条 == 原 SQL 的 ROW_NUMBER ORDER BY update_flag DESC rn=1
    dv = src["dividend"]
    ed, ex = pd.to_datetime(dv["end_date"]), pd.to_datetime(dv["ex_date"])
    dv = dv.loc[(ed >= lo) & (ed <= s) & (ex <= s)]
    if not dv.empty:
        dv = dv.sort_values("update_flag", na_position="first").drop_duplicates(
            ["ts_code", "end_date"], keep="last"
        )
    # update_flag 是 fast 预载的辅助列，原 SQL 输出无此列；空集时也要 drop
    if "update_flag" in dv.columns:
        dv = dv.drop(columns=["update_flag"])
    out["dividend"] = dv

    # mv: trade_date in [lo,s]
    mvx = src["mv"]
    td = pd.to_datetime(mvx["trade_date"])
    out["mv"] = mvx.loc[(td >= lo) & (td <= s)]
    return out


def bulk_fast(calc, todo: List[str]) -> None:
    """fast 主循环：预载一次 -> 逐月 PIT 过滤 -> process_data -> 落库+推水位。

    写入语义（2026-10-05 改）：按 snapshot_date 分区覆盖（同事务 DELETE 该月 +
    INSERT），不是 upsert。理由：本路径每月输出 = 该月完整分区（全部股票 ×
    当时点全部报告），满足分区覆盖的前提；重刷月份（改 lookback/PIT 逻辑）时
    精确替换、不留旧逻辑脏行。注意前提：本路径不支持 entity_list 子集写入——
    若将来需要子集写入，走计算器的 upsert 路径（upsert 对子集安全）。
    """
    from config.database import overwrite_by_partition

    src = _preload_sources(calc, todo[0], todo[-1])
    t0 = time.time()
    for i, snap in enumerate(todo, 1):
        sub = _pit_subsets(src, calc, snap)
        result = calc.process_data(sub, snapshot_date=snap)
        if result is not None and not result.empty:
            overwrite_by_partition(
                result,
                calc.table_name,
                partition_col="snapshot_date",
                primary_keys=["snapshot_date", "ts_code", "end_date"],
            )
            calc._set_biz_date(snap, len(result))
            logger.info("%s 处理 %d 行（分区覆盖写入）", snap, len(result))
        else:
            logger.warning("%s 空结果，跳过", snap)
        if i % 12 == 0 or i == len(todo):
            el = time.time() - t0
            logger.info("进度 %d/%d，累计 %.1f 分钟", i, len(todo), el / 60)
    logger.info("fast 回补完成：%d 个月末，耗时 %.1f 分钟", len(todo), (time.time() - t0) / 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="财务快照月频回补")
    parser.add_argument("--start", type=str, help="起始日期 yyyymmdd（含），快照取区间内月末")
    parser.add_argument("--end", type=str, help="结束日期 yyyymmdd（含），默认今天")
    parser.add_argument("--lookback", type=int, default=2, help="每快照回看自然年数（默认 2）")
    parser.add_argument("--force", action="store_true", help="重刷已有快照月（默认跳过）")
    parser.add_argument("--no-fast", dest="no_fast", action="store_true",
                        help="退回逐月 calc.update 路径（每月 6 次全表扫，慢但走原代码路径）")
    parser.add_argument("--dry-run", action="store_true", help="只体检+列计划，不写库")
    args = parser.parse_args()

    if not check_prereq():
        return 1

    from core.dates import get_today_str
    from data.panel.financial_statements_snapshot import FinancialStatementsSnapshotCalculator

    calc = FinancialStatementsSnapshotCalculator(lookback_years=args.lookback)
    today = get_today_str()
    end = args.end or today

    # 无 start = 增量（水位续跑到今天，月末语义由计算器保证）
    if not args.start:
        logger.info("增量模式：从水位续跑到 %s", end)
        if args.dry_run:
            logger.info("[DRY] 跳过执行")
            return 0
        calc.update(end_date=end)
        return 0

    month_ends: List[str] = calc._month_end_dates(args.start, end)
    if not month_ends:
        logger.warning("区间内无月末交易日")
        return 0

    have = set() if args.force else existing_snapshots()
    todo = [m for m in month_ends if m not in have]
    logger.info(
        "计划快照 %d 个月末（%s -> %s），已有 %d，待跑 %d",
        len(month_ends), month_ends[0], month_ends[-1],
        len(month_ends) - len(todo), len(todo),
    )
    for m in month_ends:
        logger.info("  %s %s", m, "[SKIP existing]" if m in have else "")
    if args.dry_run:
        logger.info("[DRY] 到此为止，未写库")
        return 0

    t0 = time.time()
    if args.no_fast or len(todo) <= 1:
        for i, snap in enumerate(todo, 1):
            calc.update(snap, snap)   # 单月一次调用：逐月落库+推水位
            if i % 12 == 0 or i == len(todo):
                el = time.time() - t0
                logger.info("进度 %d/%d，累计 %.1f 分钟", i, len(todo), el / 60)
        logger.info("回补完成：%d 个月末，耗时 %.1f 分钟", len(todo), (time.time() - t0) / 60)
    else:
        bulk_fast(calc, todo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
