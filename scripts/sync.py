"""一键同步：API 接入层(ingest) → 加工层(compute) → 缺口体检。

用法：
    # 开机一键（增量 catch-up + 拓扑计算 + 体检报告）
    scripts\py.bat scripts\sync.py

    # 区间回补（透传给两层；行情类按年分块）
    scripts\py.bat scripts\sync.py --start 20100101 --end 20261004

    # 只跑 API 层 / 只跑加工层
    scripts\py.bat scripts\sync.py --api-only
    scripts\py.bat scripts\sync.py --compute-only

    # 透传 --only（API 用接口名，加工层用 task_id）
    scripts\py.bat scripts\sync.py --only-api index_daily
    scripts\py.bat scripts\sync.py --only-compute panel:mv_monthly

顺序：ingest.py（API，trade_cal 最先）→ compute.py（加工层，拓扑序）→
      check_registry_and_gaps.py（全表体检 + 补数建议）。前一步失败默认继续，
      最后体检报告汇总真实状态（以数据库为准，不信脚本退出码）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable  # 当前 venv python（py.bat 已激活）


def run(cmd: list, title: str, fatal: bool = False) -> int:
    print(f"\n{'=' * 72}\n[RUN] {title}\n{'=' * 72}", flush=True)
    rc = subprocess.call(cmd, cwd=str(ROOT))
    print(f"[{'OK' if rc == 0 else 'WARN'}] {title} 退出码 {rc}", flush=True)
    if fatal and rc != 0:
        sys.exit(rc)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="一键同步（API + 加工层 + 体检）")
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd），不传=增量")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd），默认今天")
    parser.add_argument("--api-only", action="store_true", help="只跑 API 层")
    parser.add_argument("--compute-only", action="store_true", help="只跑加工层")
    parser.add_argument("--only-api", type=str, help="透传给 ingest.py --only")
    parser.add_argument("--only-compute", type=str, help="透传给 compute.py --only")
    parser.add_argument("--skip-check", action="store_true", help="跳过最后的体检")
    args = parser.parse_args()

    if not args.compute_only:
        cmd = [PY, "scripts/ingest.py"]
        if args.start:
            cmd += ["--start", args.start]
        if args.end:
            cmd += ["--end", args.end]
        if args.only_api:
            cmd += ["--only", args.only_api]
        run(cmd, "API 接入层（ingest.py）")

    if not args.api_only:
        cmd = [PY, "scripts/compute.py"]
        if args.start:
            cmd += ["--start", args.start]
        if args.end:
            cmd += ["--end", args.end]
        if args.only_compute:
            cmd += ["--only", args.only_compute]
        run(cmd, "加工层（compute.py，拓扑序）")

    if not args.skip_check:
        run([PY, "scripts/check_registry_and_gaps.py"], "缺口体检（check_registry_and_gaps.py）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
