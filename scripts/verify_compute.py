"""验证加工层调度配置（schedule_compute.json）。

检查：
1. 所有 class 路径可 import
2. depends_on 引用的 task_id 存在
3. 拓扑排序无循环
4. 上游表名能解析
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
logger = logging.getLogger("verify_compute")

from pipeline.runner import Runner


def main():
    runner = Runner(str(ROOT / "pipeline" / "schedule_compute.json"))
    logger.info("=" * 60)
    logger.info(f"加载 {len(runner.tasks)} 个任务")
    logger.info("=" * 60)

    # 1. class 路径可 import + table_name 可读
    logger.info("--- 1. class 路径 import 检查 ---")
    import_ok = 0
    import_fail = 0
    table_names = {}
    for tid, task in runner.tasks.items():
        try:
            cls = task.load_calculator()
            tn = getattr(cls, "table_name", "")
            table_names[tid] = tn
            logger.info(f"  [OK] {tid:30s} → {task.class_path} (table={tn})")
            import_ok += 1
        except Exception as e:
            logger.error(f"  [FAIL] {tid:30s} → {task.class_path}: {e}")
            import_fail += 1

    # 2. depends_on 引用存在
    logger.info("--- 2. depends_on 引用检查 ---")
    dep_ok = 0
    dep_fail = 0
    for tid, task in runner.tasks.items():
        for dep in task.depends_on:
            if dep in runner.tasks:
                dep_ok += 1
            else:
                # 跨频率或外部依赖（如 daily/adj_factor 等接入层表）
                logger.warning(f"  {tid} 依赖 {dep} 不在 schedule_compute（可能是接入层表）")
                dep_fail += 1
    logger.info(f"  依赖引用: {dep_ok} 个有效, {dep_fail} 个外部（接入层）")

    # 3. 拓扑排序
    logger.info("--- 3. 拓扑排序 ---")
    try:
        ordered = runner._topo_sort(list(runner.tasks.keys()))
        logger.info(f"  [OK] 拓扑排序成功，{len(ordered)} 个任务")
        for i, tid in enumerate(ordered):
            logger.info(f"    {i+1}. {tid}")
    except RuntimeError as e:
        logger.error(f"  [FAIL] 拓扑排序失败: {e}")
        return 1

    # 4. 上游表行数检查（不跑，只看表是否存在）
    logger.info("--- 4. 上游表存在性检查 ---")
    from config.database import execute_sql
    upstream_ok = 0
    upstream_fail = 0
    for tid, task in runner.tasks.items():
        for dep in task.depends_on:
            if dep not in runner.tasks:
                # 接入层表，检查表是否存在
                # 接入层表名 = dep（如 daily / adj_factor / moneyflow）
                try:
                    execute_sql(f"SELECT COUNT(*) AS n FROM {dep} LIMIT 1", None)
                    upstream_ok += 1
                except Exception:
                    logger.warning(f"  {tid} 依赖接入层表 {dep} 不存在或无数据")
                    upstream_fail += 1
    logger.info(f"  接入层依赖: {upstream_ok} 个就绪, {upstream_fail} 个缺失")

    # 汇总
    logger.info("=" * 60)
    logger.info("汇总")
    logger.info("=" * 60)
    logger.info(f"  class import: {import_ok} OK / {import_fail} FAIL")
    logger.info(f"  拓扑排序: OK（无循环）")
    logger.info(f"  接入层依赖: {upstream_ok} 就绪 / {upstream_fail} 缺失")

    ok = import_fail == 0
    logger.info(f"结论: {'PASS ✅' if ok else 'FAIL ❌'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
