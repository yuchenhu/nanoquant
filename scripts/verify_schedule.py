"""验证调度系统（pipeline/runner.py + schedule_ingest.json）。

检查：
1. Runner 能加载 schedule_ingest.json
2. 所有 class 路径可 import + table_name 可读
3. 拓扑排序无循环
4. 拓扑顺序合理（trade_cal → stock_basic → 下游）
5. 端到端：Runner.run(only=trade_cal) 单任务能跑通
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_schedule")

from pipeline.runner import Runner


def main():
    # ===== 1. 加载 schedule_ingest.json =====
    logger.info("=" * 60)
    logger.info("1. 加载 schedule_ingest.json")
    logger.info("=" * 60)
    runner = Runner(str(ROOT / "pipeline" / "schedule_ingest.json"))
    logger.info(f"加载 {len(runner.tasks)} 个任务")

    # 按频率统计
    from collections import Counter
    freq_cnt = Counter(t.frequency for t in runner.tasks.values())
    for f, n in freq_cnt.items():
        logger.info(f"  {f}: {n} 个任务")

    # ===== 2. class 路径 import 检查 =====
    logger.info("=" * 60)
    logger.info("2. class 路径 import 检查")
    logger.info("=" * 60)
    import_ok = 0
    import_fail = 0
    for tid, task in runner.tasks.items():
        try:
            cls = task.load_calculator()
            tn = getattr(cls, "table_name", "")
            logger.info(f"  [OK] {tid:25s} → {task.class_path} (table={tn})")
            import_ok += 1
        except Exception as e:
            logger.error(f"  [FAIL] {tid:25s} → {task.class_path}: {e}")
            import_fail += 1

    # ===== 3. 拓扑排序 =====
    logger.info("=" * 60)
    logger.info("3. 拓扑排序（全频率合并）")
    logger.info("=" * 60)
    try:
        ordered = runner._topo_sort(list(runner.tasks.keys()))
        logger.info(f"[OK] 拓扑排序成功，{len(ordered)} 个任务")
        for i, tid in enumerate(ordered):
            task = runner.tasks[tid]
            deps = task.depends_on
            logger.info(f"  {i+1:2d}. {tid:25s} [{task.frequency:10s}] deps={deps}")
    except RuntimeError as e:
        logger.error(f"[FAIL] 拓扑排序失败: {e}")
        return 1

    # ===== 4. 拓扑顺序合理性检查 =====
    logger.info("=" * 60)
    logger.info("4. 拓扑顺序合理性检查")
    logger.info("=" * 60)
    pos = {tid: i for i, tid in enumerate(ordered)}
    order_ok = True
    for tid, task in runner.tasks.items():
        for dep in task.depends_on:
            if dep in pos and pos[dep] >= pos[tid]:
                logger.error(f"  [FAIL] {tid} (pos={pos[tid]}) 依赖 {dep} (pos={pos[dep]})，顺序错误")
                order_ok = False
    if order_ok:
        logger.info("  [OK] 所有依赖都在被依赖者之前")

    # ===== 5. 端到端：Runner.run(only=trade_cal) =====
    logger.info("=" * 60)
    logger.info("5. 端到端：Runner.run(only=trade_cal) 单任务")
    logger.info("=" * 60)
    try:
        results = runner.run(only="trade_cal")
        if not results:
            logger.error("  [FAIL] Runner.run 返回空结果")
            return 1
        for tid, r in results.items():
            logger.info(f"  {tid}: {r}")
        ok = all(r["status"] == "ok" for r in results.values())
        logger.info(f"  [{'OK' if ok else 'FAIL'}] 端到端调度")
    except Exception as e:
        logger.error(f"  [FAIL] Runner.run 异常: {e}", exc_info=True)
        return 1

    # ===== 汇总 =====
    logger.info("=" * 60)
    logger.info("汇总")
    logger.info("=" * 60)
    logger.info(f"  class import: {import_ok} OK / {import_fail} FAIL")
    logger.info(f"  拓扑排序: OK（无循环）")
    logger.info(f"  拓扑顺序: {'OK' if order_ok else 'FAIL'}")
    logger.info(f"  端到端调度: {'OK' if ok else 'FAIL'}")

    all_pass = import_fail == 0 and order_ok and ok
    logger.info(f"结论: {'PASS ✅' if all_pass else 'FAIL ❌'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
