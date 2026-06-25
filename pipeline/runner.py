"""JSON 配置驱动的调度执行器。

调度模型（见 CLAUDE.md 2.5 / 2.11）：
- schedule_*.json 按 frequency（daily/weekly/monthly/irregular）组织任务
- 每个任务声明：task_id / class（module.Class 路径）/ depends_on / params
- runner 拓扑排序 + 上游表行数检查 + 调用 Calculator.update()

用法（被 scripts/run_ingest.py 和 run_compute.py 调用）：
    runner = Runner("pipeline/schedule_ingest.json")
    runner.run(start_date=None, end_date=None)            # 增量
    runner.run(start_date="20210101", end_date="20251231")  # 回补
    runner.run(only="data.etl.equities.daily.StockDailyCalculator")  # 单任务
"""
from __future__ import annotations

import importlib
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """单个调度任务。"""
    task_id: str
    class_path: str          # module.Class 完整路径
    frequency: str           # daily / weekly / monthly / irregular
    depends_on: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def load_calculator(self):
        """动态加载 Calculator 类。"""
        module_path, _, class_name = self.class_path.rpartition(".")
        if not module_path or not class_name:
            raise ValueError(f"task {self.task_id} class_path 非法: {self.class_path}")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls


class Runner:
    """调度执行器。

    schedule JSON 结构：
    {
      "daily":    [{"task_id": "...", "class": "module.Class", "depends_on": [...], "params": {...}}],
      "weekly":   [...],
      "monthly":  [...],
      "irregular":[...]
    }
    """

    def __init__(self, schedule_path: str):
        self.schedule_path = Path(schedule_path)
        if not self.schedule_path.exists():
            raise FileNotFoundError(f"调度配置不存在: {self.schedule_path}")
        self.tasks: Dict[str, Task] = self._load_schedule()

    def _load_schedule(self) -> Dict[str, Task]:
        """加载 schedule JSON，展平为 task_id → Task。"""
        with open(self.schedule_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        tasks: Dict[str, Task] = {}
        for frequency, task_list in config.items():
            if not isinstance(task_list, list):
                continue
            for item in task_list:
                task_id = item.get("task_id") or item.get("id")
                if not task_id:
                    logger.warning("跳过无 task_id 的任务: %s", item)
                    continue
                tasks[task_id] = Task(
                    task_id=task_id,
                    class_path=item["class"],
                    frequency=frequency,
                    depends_on=item.get("depends_on", []),
                    params=item.get("params", {}),
                    description=item.get("description", ""),
                )
        logger.info("从 %s 加载 %d 个任务", self.schedule_path.name, len(tasks))
        return tasks

    # ===== 拓扑排序 =====

    def _topo_sort(self, task_ids: List[str]) -> List[str]:
        """Kahn 拓扑排序。检测循环依赖。"""
        in_degree: Dict[str, int] = {t: 0 for t in task_ids}
        graph: Dict[str, List[str]] = defaultdict(list)

        task_set: Set[str] = set(task_ids)
        for tid in task_ids:
            task = self.tasks[tid]
            for dep in task.depends_on:
                if dep not in task_set:
                    logger.warning(
                        "task %s 依赖 %s 不在本次执行集（可能跨频率），忽略",
                        tid, dep,
                    )
                    continue
                graph[dep].append(tid)
                in_degree[tid] += 1

        queue = deque([t for t in task_ids if in_degree[t] == 0])
        sorted_ids: List[str] = []
        while queue:
            t = queue.popleft()
            sorted_ids.append(t)
            for nxt in graph[t]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(sorted_ids) != len(task_ids):
            cycle = [t for t in task_ids if in_degree[t] > 0]
            raise RuntimeError(f"检测到循环依赖: {cycle}")

        return sorted_ids

    # ===== 上游表行数检查 =====

    def _check_upstream_ready(self, task: Task) -> bool:
        """检查上游依赖表是否有数据（行数 > 0）。"""
        if not task.depends_on:
            return True

        from sqlalchemy import text
        from config.database import engine

        for dep_id in task.depends_on:
            dep_task = self.tasks.get(dep_id)
            if dep_task is None:
                continue  # 跨频率依赖，已在 _topo_sort 警告
            # dep_task 的目标表名 = 实例化后读 table_name（轻量，不连库拉数）
            try:
                dep_cls = dep_task.load_calculator()
                # 类属性 table_name 不需要实例化即可读
                dep_table = getattr(dep_cls, "table_name", "")
                if not dep_table:
                    continue
                with engine.connect() as conn:
                    row = conn.execute(
                        text(f"SELECT COUNT(*) FROM {dep_table}")
                    ).fetchone()
                    count = row[0] if row else 0
                if count == 0:
                    logger.warning(
                        "task %s 上游 %s（表 %s）无数据，跳过",
                        task.task_id, dep_id, dep_table,
                    )
                    return False
            except Exception as e:
                logger.debug(
                    "task %s 上游检查 %s 失败（放行）: %s",
                    task.task_id, dep_id, e,
                )
                continue
        return True

    # ===== 执行 =====

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        only: Optional[str] = None,
        **extra_params: Any,
    ) -> Dict[str, Any]:
        """执行调度。

        - start_date/end_date: biz_date 区间。None=增量（各任务从水位续跑）
        - only: 只跑指定 class_path 的任务（跨频率搜索，单任务调试用）
        - extra_params: 透传给所有任务的额外参数
        - 返回 {task_id: {"rows": int, "status": "ok"/"skipped"/"error"}} 摘要
        """
        # 筛选要执行的任务
        if only:
            target_ids = [
                tid for tid, t in self.tasks.items()
                if t.class_path == only or tid == only
            ]
            if not target_ids:
                logger.error("--only=%s 未匹配任何任务（跨频率搜索后仍无）", only)
                return {}
            logger.info("--only 模式：匹配 %d 个任务", len(target_ids))
        else:
            target_ids = list(self.tasks.keys())

        # 拓扑排序
        try:
            ordered = self._topo_sort(target_ids)
        except RuntimeError as e:
            logger.error("拓扑排序失败: %s", e)
            return {}

        results: Dict[str, Any] = {}
        for tid in ordered:
            task = self.tasks[tid]
            results[tid] = self._run_task(task, start_date, end_date, extra_params)

        # 摘要
        ok = sum(1 for r in results.values() if r["status"] == "ok")
        skipped = sum(1 for r in results.values() if r["status"] == "skipped")
        errored = sum(1 for r in results.values() if r["status"] == "error")
        logger.info(
            "调度完成: %d ok / %d skipped / %d error / %d total",
            ok, skipped, errored, len(results),
        )
        return results

    def _run_task(
        self,
        task: Task,
        start_date: Optional[str],
        end_date: Optional[str],
        extra_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行单个任务。"""
        logger.info("▶ task %s [%s] %s", task.task_id, task.frequency, task.description)

        # 上游检查
        if not self._check_upstream_ready(task):
            return {"status": "skipped", "rows": 0, "reason": "upstream_empty"}

        # 合并参数：task.params + extra_params（extra 优先）
        params = {**task.params, **extra_params}

        try:
            calc_cls = task.load_calculator()
            calc = calc_cls()
            df = calc.update(start_date=start_date, end_date=end_date, **params)
            rows = len(df) if df is not None else 0
            logger.info("✓ task %s 完成，%d 行", task.task_id, rows)
            return {"status": "ok", "rows": rows}
        except Exception as e:
            logger.exception("✗ task %s 失败: %s", task.task_id, e)
            return {"status": "error", "rows": 0, "error": str(e)}
