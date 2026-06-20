"""Step 3 pipeline 脚手架冒烟测试（mock 第三方库，验证 import + 拓扑排序 + 策略基类）。"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

# ===== Mock 第三方库 =====
for mod_name in [
    "pandas", "numpy", "sqlalchemy", "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.insert", "pymysql", "dotenv", "statsmodels",
    "statsmodels.api",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

import sqlalchemy
sqlalchemy.create_engine = MagicMock(return_value=MagicMock())
sqlalchemy.inspect = MagicMock()
sqlalchemy.text = MagicMock()
sqlalchemy.MetaData = MagicMock()
sqlalchemy.Table = MagicMock()

# ===== 1. import 链 =====
print("=== 1. import 链 ===")
failures = []
for mod in [
    "pipeline",
    "pipeline.incremental",
    "pipeline.incremental.base",
    "pipeline.incremental.by_trade_date",
    "pipeline.incremental.by_ann_date",
    "pipeline.incremental.full_refresh",
    "pipeline.runner",
]:
    try:
        __import__(mod)
        print(f"  OK  {mod}")
    except Exception as e:
        failures.append((mod, e))
        print(f"  FAIL {mod}: {type(e).__name__}: {e}")

# ===== 2. 三类策略基类属性 =====
print("\n=== 2. 三类增量策略基类 ===")
from pipeline.incremental import (
    BaseIncremental, ByTradeDateCalculator, ByAnnDateCalculator, FullRefreshCalculator
)

assert ByTradeDateCalculator.biz_date_col == "trade_date", "by_trade_date 应 biz_date_col=trade_date"
assert ByAnnDateCalculator.biz_date_col == "ann_date", "by_ann_date 应 biz_date_col=ann_date"
assert FullRefreshCalculator.biz_date_col == "", "full_refresh 应 biz_date_col 空"
assert FullRefreshCalculator.write_mode == "truncate", "full_refresh 应 write_mode=truncate"
print(f"  OK  ByTradeDateCalculator.biz_date_col = {ByTradeDateCalculator.biz_date_col!r}")
print(f"  OK  ByAnnDateCalculator.biz_date_col = {ByAnnDateCalculator.biz_date_col!r}")
print(f"  OK  ByAnnDateCalculator.lookback_days = {ByAnnDateCalculator.lookback_days}")
print(f"  OK  FullRefreshCalculator.biz_date_col = {FullRefreshCalculator.biz_date_col!r}")
print(f"  OK  FullRefreshCalculator.write_mode = {FullRefreshCalculator.write_mode!r}")

# 都继承 BaseIncremental 且有 fetch_one_period 抽象
from core.calculator import BaseCalculator
assert issubclass(ByTradeDateCalculator, BaseIncremental)
assert issubclass(ByAnnDateCalculator, BaseIncremental)
assert issubclass(FullRefreshCalculator, BaseIncremental)
assert issubclass(BaseIncremental, BaseCalculator)
print("  OK  三类策略 → BaseIncremental → BaseCalculator 继承链")

# ===== 3. schedule JSON 合法性 =====
print("\n=== 3. schedule JSON ===")
for fname in ["schedule_ingest.json", "schedule_compute.json"]:
    fpath = Path("pipeline") / fname
    with open(fpath, "r", encoding="utf-8") as f:
        config = json.load(f)
    total = sum(len(v) for v in config.values() if isinstance(v, list))
    freqs = [k for k in config if isinstance(config[k], list)]
    print(f"  OK  {fname}: {total} 个任务，频率分组 {freqs}")
    # 每个任务必须有 task_id + class
    for freq, tasks in config.items():
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            assert "task_id" in t, f"{fname}/{freq} 缺 task_id: {t}"
            assert "class" in t, f"{fname}/{freq}[{t.get('task_id')}] 缺 class"
            assert "." in t["class"], f"{fname}/{freq}[{t['task_id']}] class 非法: {t['class']}"

# ===== 4. Runner 加载 + 拓扑排序 =====
print("\n=== 4. Runner 加载 + 拓扑排序 ===")
from pipeline.runner import Runner, Task

runner = Runner("pipeline/schedule_ingest.json")
print(f"  OK  Runner 加载 {len(runner.tasks)} 个任务")

# 拓扑排序（全量）
all_ids = list(runner.tasks.keys())
ordered = runner._topo_sort(all_ids)
assert len(ordered) == len(all_ids), "拓扑排序后数量应一致"
print(f"  OK  拓扑排序 {len(ordered)} 个任务（无循环依赖）")

# trade_cal 应在 stock_basic 之前（依赖关系）
tc_idx = ordered.index("trade_cal")
sb_idx = ordered.index("stock_basic")
assert tc_idx < sb_idx, f"trade_cal({tc_idx}) 应在 stock_basic({sb_idx}) 之前"
print(f"  OK  依赖顺序: trade_cal({tc_idx}) < stock_basic({sb_idx})")

# ===== 5. Runner --only 跨频率搜索 =====
print("\n=== 5. Runner --only 跨频率搜索 ===")
# monthly 里的 monthly 任务
target = [tid for tid, t in runner.tasks.items() if t.class_path == "data.etl.loader.StockMonthlyCalculator"]
assert target == ["monthly"], f"应匹配 monthly，实际 {target}"
print(f"  OK  --only=StockMonthlyCalculator 匹配 {target}（跨 daily/monthly 搜索）")

# ===== 6. 循环依赖检测 =====
print("\n=== 6. 循环依赖检测 ===")
runner2 = Runner("pipeline/schedule_ingest.json")
# 手动造循环：stock_basic 依赖 daily，daily 依赖 stock_basic
runner2.tasks["stock_basic"].depends_on = ["daily"]
runner2.tasks["daily"].depends_on = ["stock_basic"]
try:
    runner2._topo_sort(["stock_basic", "daily"])
    print("  FAIL 未检测到循环依赖")
    failures.append(("cycle_detection", "未抛异常"))
except RuntimeError as e:
    print(f"  OK  检测到循环依赖: {e}")

# ===== 结果 =====
print("\n=== 结果 ===")
if failures:
    print(f"FAILED: {len(failures)} 项")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL OK")
