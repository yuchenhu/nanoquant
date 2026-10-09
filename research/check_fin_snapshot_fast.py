# -*- coding: utf-8 -*-
"""fast 批量路径 vs 原路径 等价性验收（2026-10-04）。

背景：backfill_fin_snapshot.py 新增 fast 路径（六源一次预载 + 逐月 pandas PIT
过滤）替代原路径（每月 6 次全表扫描）。本脚本对样本月份分别走两条路径的
process_data（不落库），断言输出完全一致。

样本月覆盖三种数据形态：
  20100630 - 早期（dividend 无数据，mv 部分缺失）
  20150630 - 中期（dividend 部分覆盖）
  20230630 - 近期（六源全齐）

用法：python scripts/py.bat research/check_fin_snapshot_fast.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE_SNAPS = ["20100630", "20150630", "20230630"]


def main() -> int:
    from data.panel.financial_statements_snapshot import FinancialStatementsSnapshotCalculator
    from scripts.backfill_fin_snapshot import _pit_subsets, _preload_sources

    calc = FinancialStatementsSnapshotCalculator()

    t0 = time.time()
    src = _preload_sources(calc, SAMPLE_SNAPS[0], SAMPLE_SNAPS[-1])
    t_pre = time.time() - t0

    all_ok = True
    for snap in SAMPLE_SNAPS:
        # 路径 A：原路径（get_data 走 SQL，每月 6 次扫描）
        t1 = time.time()
        raw_a = calc.get_data(snap, snap)
        df_a = calc.process_data(raw_a, snapshot_date=snap)
        t_a = time.time() - t1

        # 路径 B：fast 路径（预载 + pandas 过滤）
        t2 = time.time()
        raw_b = _pit_subsets(src, calc, snap)
        df_b = calc.process_data(raw_b, snapshot_date=snap)
        t_b = time.time() - t2

        # 比对：行数 -> 列序 -> 排序后逐值
        if df_a is None or df_b is None:
            print(f"[ERR] {snap}: 某路径返回 None (A={'None' if df_a is None else len(df_a)}, "
                  f"B={'None' if df_b is None else len(df_b)})")
            all_ok = False
            continue
        if len(df_a) != len(df_b):
            print(f"[ERR] {snap}: 行数不等 A={len(df_a)} B={len(df_b)}")
            all_ok = False
            continue
        if list(df_a.columns) != list(df_b.columns):
            sa, sb = set(df_a.columns), set(df_b.columns)
            print(f"[ERR] {snap}: 列序不等 "
                  f"(A独有={sorted(sa-sb)}, B独有={sorted(sb-sa)}, "
                  f"顺序差={[i for i,(x,y) in enumerate(zip(df_a.columns, df_b.columns)) if x!=y][:5]})")
            all_ok = False
            continue
        a = df_a.sort_values(["ts_code", "end_date"], kind="stable").reset_index(drop=True)
        b = df_b.sort_values(["ts_code", "end_date"], kind="stable").reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                a, b, check_dtype=False, check_exact=False, rtol=1e-9, atol=0
            )
            print(f"[OK] {snap}: {len(a)} 行 x {len(a.columns)} 列 完全一致 "
                  f"(原路径 {t_a:.1f}s, fast {t_b:.1f}s)")
        except AssertionError as e:
            msg = str(e).split(chr(10))[0]
            print(f"[ERR] {snap}: 不一致 -> {msg}")
            # 定位差异列
            for c in a.columns:
                sa, sb = a[c].astype(str), b[c].astype(str)
                neq = ~sa.eq(sb)
                if neq.any():
                    print(f"      差异列 {c}: {int(neq.sum())} 行")
            all_ok = False

    print(f"预载耗时 {t_pre:.1f}s（一次性）")
    print("[OK] 全部一致，fast 路径可放行" if all_ok else "[ERR] 存在不一致，禁止使用 fast 路径")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
