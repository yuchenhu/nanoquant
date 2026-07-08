"""加工层计算运行脚本（v2：按 schedule_compute.json 依赖拓扑排序执行）。

功能：读 pipeline/schedule_compute.json，合并 daily+monthly 任务，
     按 depends_on 拓扑排序后严格依序执行。

用法：
    # 全量跑所有加工层（拓扑排序，依赖先行）
    python scripts/run_compute.py

    # 指定 biz_date 区间
    python scripts/run_compute.py --start 20240101 --end 20240131

    # 只跑某一层（拓扑排序，但只包含该层任务 + 其传递依赖）
    python scripts/run_compute.py --layer panel

    # 只跑指定 calculator（不使用拓扑排序，用户自行保证依赖）
    python scripts/run_compute.py --only panel:stock_daily,factor:high_low_spread

    # 列出所有可用 calculator
    python scripts/run_compute.py --list
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_compute")

SCHEDULE_PATH = ROOT / "pipeline" / "schedule_compute.json"


# ============================================================
# 依赖拓扑排序
# ============================================================

def _load_schedule() -> List[dict]:
    """加载 schedule_compute.json，合并 daily+monthly 为一个列表。"""
    if not SCHEDULE_PATH.exists():
        logger.error("schedule_compute.json 不存在: %s", SCHEDULE_PATH)
        return []
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        schedule = json.load(f)
    tasks = []
    for section in ["daily", "monthly"]:
        for task in schedule.get(section, []):
            task["_section"] = section
            tasks.append(task)
    return tasks


def _topological_sort(
    tasks: List[dict],
    only_ids: Optional[Set[str]] = None,
    layer_filter: Optional[str] = None,
) -> List[dict]:
    """按 depends_on 拓扑排序，返回有序的 task 列表。

    规则：
    - depends_on 中匹配其他 task_id 的 → 计算依赖（严格先后）
    - depends_on 中不匹配 task_id 的 → 接入层表（跳过，假设已就绪）
    - only_ids 不为空 → 只跑这些任务 + 其传递依赖
    - layer_filter 不为空 → 只包含该层任务（按 class 路径前缀过滤）
    """
    task_map: Dict[str, dict] = {t["task_id"]: t for t in tasks}
    all_task_ids = set(task_map.keys())

    # 确定要跑的任务集合
    if only_ids:
        # 展开传递依赖
        run_ids: Set[str] = set(only_ids)
        changed = True
        while changed:
            changed = False
            for tid in list(run_ids):
                t = task_map.get(tid)
                if not t:
                    continue
                for dep in t.get("depends_on", []):
                    if dep in all_task_ids and dep not in run_ids:
                        run_ids.add(dep)
                        changed = True
        unknown = only_ids - all_task_ids
        if unknown:
            logger.warning("以下 task_id 不在 schedule 中，跳过: %s", unknown)
    elif layer_filter:
        prefix = f"data.{layer_filter}."
        run_ids = {t["task_id"] for t in tasks if t["class"].startswith(prefix)}
    else:
        run_ids = all_task_ids.copy()

    # 构建邻接表（只包含计算依赖）
    in_degree: Dict[str, int] = defaultdict(int)
    adj: Dict[str, List[str]] = defaultdict(list)

    for tid in run_ids:
        t = task_map.get(tid)
        if not t:
            continue
        for dep in t.get("depends_on", []):
            if dep in run_ids:  # 只处理 compute→compute 依赖
                adj[dep].append(tid)
                in_degree[tid] += 1

    # Kahn 算法
    queue = deque(sorted(tid for tid in run_ids if in_degree[tid] == 0))
    ordered: List[str] = []
    while queue:
        tid = queue.popleft()
        ordered.append(tid)
        for neighbor in sorted(adj[tid]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered) != len(run_ids):
        missing = run_ids - set(ordered)
        logger.error("依赖循环或缺失: %s，降级为原始顺序", missing)
        ordered = list(run_ids)

    # 还原为 task dict 列表
    return [task_map[tid] for tid in ordered if tid in task_map]


# ============================================================
# 类加载
# ============================================================

def _import_class(class_path: str) -> Optional[type]:
    """从 'data.panel.stock_daily_panel.StockDailyPanelCalculator' 导入类。"""
    try:
        parts = class_path.rsplit(".", 1)
        if len(parts) != 2:
            logger.error("无效 class 路径: %s", class_path)
            return None
        module_name, cls_name = parts
        module = importlib.import_module(module_name)
        return getattr(module, cls_name, None)
    except ModuleNotFoundError:
        logger.debug("模块未实现，跳过: %s", class_path)
        return None
    except Exception as e:
        logger.warning("导入 %s 失败: %s", class_path, e)
        return None


# ============================================================
# 主逻辑
# ============================================================

def list_calculators() -> None:
    """列出 schedule_compute.json 中所有任务。"""
    tasks = _load_schedule()
    if not tasks:
        print("schedule_compute.json 为空或不存在")
        return

    ordered = _topological_sort(tasks)
    print("=" * 70)
    print(f"schedule_compute.json 任务列表（拓扑排序，共 {len(ordered)} 个）")
    print("=" * 70)
    for t in ordered:
        deps = [d for d in t.get("depends_on", []) if any(
            d == o["task_id"] for o in tasks
        )]
        dep_str = f" ← {', '.join(deps)}" if deps else ""
        print(f"  [{t['_section']:7s}] {t['task_id']:35s} {t['class']}{dep_str}")
    print("\n" + "=" * 70)


def run_compute(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    layer: Optional[str] = None,
    only: Optional[List[str]] = None,
    no_topology: bool = False,
) -> int:
    """运行加工层计算。"""
    tasks = _load_schedule()
    if not tasks:
        logger.error("schedule_compute.json 为空，无法运行")
        return 1

    # 解析 --only
    only_ids: Optional[Set[str]] = None
    if only:
        # --only 格式: panel:stock_daily,factor:high_low_spread
        #   → 需要映射到 task_id。遍历 schedule 找 layer:name 匹配。
        only_ids = set()
        for item in only:
            parts = item.split(":", 1)
            if len(parts) == 2:
                l, name = parts
                matched = False
                for t in tasks:
                    layer_prefix = t["class"].split(".")[1]  # "data.panel.xxx" → "panel"
                    class_name = t["class"].rsplit(".", 1)[-1]
                    # 尝试多种匹配方式
                    if t["task_id"] == name or t["task_id"] == f"{name}_panel" or \
                       f"{l}:{name}" == f"{layer_prefix}:{t['task_id']}":
                        only_ids.add(t["task_id"])
                        matched = True
                        break
                    # 宽松匹配：类名包含关键词
                    cls_lower = class_name.lower()
                    if name.lower() in cls_lower:
                        # 再验证 layer
                        cls_module = t["class"]
                        if l in cls_module:
                            only_ids.add(t["task_id"])
                            matched = True
                            break
                if not matched:
                    logger.warning("--only 未匹配到任务: %s", item)
            else:
                # 直接按 task_id 匹配
                if item in {t["task_id"] for t in tasks}:
                    only_ids.add(item)
                else:
                    logger.warning("--only 未匹配到 task_id: %s", item)
        if not only_ids:
            logger.error("--only 未匹配到任何任务")
            return 1
        # --only 模式不使用拓扑排序（除非显式要求）
        if not no_topology:
            no_topology = True
            logger.info("--only 模式：不强制拓扑排序，请自行保证依赖顺序")

    # 拓扑排序
    if no_topology or only_ids:
        # --only 或无拓扑模式：保持用户指定顺序，但展开传递依赖
        ordered_tasks = _topological_sort(tasks, only_ids=only_ids, layer_filter=None)
        if only_ids:
            # 拓扑排序后，保持 only_ids 的顺序在前
            dep_only = set(t["task_id"] for t in ordered_tasks)
            auto_deps = dep_only - only_ids
            if auto_deps:
                logger.info("自动加入传递依赖: %s", auto_deps)
    else:
        ordered_tasks = _topological_sort(
            tasks, only_ids=None, layer_filter=layer
        )

    if not ordered_tasks:
        logger.warning("没有匹配的任务可运行")
        return 0

    # 打印执行计划
    logger.info("=" * 60)
    logger.info("执行计划（拓扑排序，共 %d 个任务）:", len(ordered_tasks))
    for i, t in enumerate(ordered_tasks):
        deps = [d for d in t.get("depends_on", []) if any(
            d == o["task_id"] for o in ordered_tasks
        )]
        dep_str = f" ← {', '.join(deps)}" if deps else ""
        logger.info("  %2d. [%s] %s%s", i + 1, t["_section"], t["task_id"], dep_str)
    logger.info("=" * 60)
    if start_date or end_date:
        logger.info("biz_date 区间: [%s, %s]", start_date or "水位次日", end_date or "今天")
    else:
        logger.info("biz_date 区间: 从水位次日续跑（增量）")

    # 按拓扑顺序执行
    success = 0
    failed = 0
    skipped = 0
    for t in ordered_tasks:
        tid = t["task_id"]
        cls_path = t["class"]

        try:
            cls = _import_class(cls_path)
            if cls is None:
                logger.info("--- [skip] %s: 类未实现 (%s) ---", tid, cls_path)
                skipped += 1
                continue

            logger.info("--- [%s] 开始: %s → %s ---", t["_section"], tid, cls.table_name)
            instance = cls()
            result = instance.update(start_date=start_date, end_date=end_date)
            rows = len(result) if result is not None else 0
            logger.info("--- [%s] 完成: %s (%d 行) ---", t["_section"], tid, rows)
            success += 1
        except Exception as e:
            logger.error("--- [%s] 失败: %s: %s ---", t["_section"], tid, e, exc_info=True)
            failed += 1
            # 严格模式：一个失败就停（避免下游在脏数据上跑）
            logger.error("上游任务失败，停止后续执行（严格模式）")
            break

    logger.info("=" * 60)
    logger.info("加工层计算完成：成功 %d，失败 %d，跳过(未实现) %d", success, failed, skipped)
    logger.info("=" * 60)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="运行加工层计算（按 schedule_compute.json 拓扑排序）"
    )
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd）")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd）")
    parser.add_argument("--layer", type=str, choices=["panel", "factor", "label"],
                        help="只跑某一层（拓扑排序，含传递依赖）")
    parser.add_argument("--only", type=str,
                        help="只跑指定 calculator（逗号分隔，格式 layer:name，如 panel:stock_daily）")
    parser.add_argument("--no-topology", action="store_true",
                        help="关闭拓扑排序（与 --only 联用时默认关闭）")
    parser.add_argument("--list", action="store_true", help="列出所有可用 calculator 后退出")
    args = parser.parse_args()

    if args.list:
        list_calculators()
        return 0

    only = args.only.split(",") if args.only else None

    return run_compute(
        start_date=args.start,
        end_date=args.end,
        layer=args.layer,
        only=only,
        no_topology=args.no_topology,
    )


if __name__ == "__main__":
    sys.exit(main())
