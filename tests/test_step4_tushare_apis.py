"""Step 4 config/tushare_apis.json 验收测试。"""
import json
from pathlib import Path

print("=== Step 4: config/tushare_apis.json 验收 ===")

fpath = Path("config/tushare_apis.json")
assert fpath.exists(), "config/tushare_apis.json 不存在"

with open(fpath, "r", encoding="utf-8") as f:
    apis = json.load(f)

# ===== 1. 22 个接口 =====
print(f"\n=== 1. 接口数量: {len(apis)} ===")
assert len(apis) == 22, f"应有 22 个接口，实际 {len(apis)}"
print(f"  OK  {len(apis)} 个接口")

# ===== 2. 每个接口必要字段 =====
print("\n=== 2. 每个接口必要字段 ===")
required_keys = {"api_name", "description", "table_name", "incremental_strategy",
                 "biz_date_col", "write_mode", "frequency", "fields", "params"}
for key, cfg in apis.items():
    missing = required_keys - set(cfg.keys())
    assert not missing, f"{key} 缺字段: {missing}"
print(f"  OK  所有接口含 {len(required_keys)} 个必要字段")

# ===== 3. incremental_strategy 合法性 =====
print("\n=== 3. incremental_strategy 合法性 ===")
valid_strategies = {"by_trade_date", "by_ann_date", "full_refresh"}
for key, cfg in apis.items():
    s = cfg["incremental_strategy"]
    assert s in valid_strategies, f"{key} strategy 非法: {s}"
print("  OK  所有 incremental_strategy 合法")

# ===== 4. biz_date_col 与 strategy 一致性 =====
print("\n=== 4. biz_date_col 与 strategy 一致性 ===")
for key, cfg in apis.items():
    s = cfg["incremental_strategy"]
    bdc = cfg["biz_date_col"]
    if s == "by_trade_date":
        assert bdc == "trade_date", f"{key} by_trade_date 应 biz_date_col=trade_date，实际 {bdc!r}"
    elif s == "by_ann_date":
        assert bdc == "ann_date", f"{key} by_ann_date 应 biz_date_col=ann_date，实际 {bdc!r}"
    elif s == "full_refresh":
        assert bdc == "", f"{key} full_refresh 应 biz_date_col=''，实际 {bdc!r}"
print("  OK  biz_date_col 与 strategy 全部一致")

# ===== 5. 策略分布 =====
print("\n=== 5. 策略分布 ===")
by_strat = {}
for cfg in apis.values():
    s = cfg["incremental_strategy"]
    by_strat[s] = by_strat.get(s, 0) + 1
for s, c in sorted(by_strat.items()):
    print(f"  {s}: {c}")
assert by_strat.get("by_trade_date", 0) == 12, f"by_trade_date 应 12 个，实际 {by_strat.get('by_trade_date')}"
assert by_strat.get("by_ann_date", 0) == 5, f"by_ann_date 应 5 个，实际 {by_strat.get('by_ann_date')}"
assert by_strat.get("full_refresh", 0) == 5, f"full_refresh 应 5 个，实际 {by_strat.get('full_refresh')}"

# ===== 6. 财务类（by_ann_date）必须有 ann_date 字段 =====
print("\n=== 6. by_ann_date 接口 fields 含 ann_date ===")
for key, cfg in apis.items():
    if cfg["incremental_strategy"] == "by_ann_date":
        assert "ann_date" in cfg["fields"], f"{key} fields 缺 ann_date"
        print(f"  OK  {key}: fields 含 ann_date")

# ===== 7. by_trade_date 接口 fields 含 trade_date =====
print("\n=== 7. by_trade_date 接口 fields 含 trade_date ===")
for key, cfg in apis.items():
    if cfg["incremental_strategy"] == "by_trade_date":
        assert "trade_date" in cfg["fields"], f"{key} fields 缺 trade_date"
print(f"  OK  所有 by_trade_date 接口 fields 含 trade_date")

# ===== 8. write_mode 与 strategy 一致性 =====
print("\n=== 8. write_mode 与 strategy 一致性 ===")
for key, cfg in apis.items():
    s = cfg["incremental_strategy"]
    wm = cfg["write_mode"]
    if s == "full_refresh":
        assert wm == "truncate", f"{key} full_refresh 应 write_mode=truncate，实际 {wm}"
    else:
        assert wm == "upsert", f"{key} {s} 应 write_mode=upsert，实际 {wm}"
print("  OK  write_mode 与 strategy 全部一致")

# ===== 9. params 非空（至少有 limit） =====
print("\n=== 9. params 合法性 ===")
for key, cfg in apis.items():
    assert isinstance(cfg["params"], dict), f"{key} params 应为 dict"
    assert "limit" in cfg["params"], f"{key} params 缺 limit"
print("  OK  所有 params 含 limit")

# ===== 10. table_name 唯一性 =====
print("\n=== 10. table_name 唯一性 ===")
table_names = [cfg["table_name"] for cfg in apis.values()]
dupes = [t for t in set(table_names) if table_names.count(t) > 1]
assert not dupes, f"table_name 重复: {dupes}"
print(f"  OK  {len(table_names)} 个 table_name 全部唯一")

print("\n=== 结果 ===")
print("ALL OK")
