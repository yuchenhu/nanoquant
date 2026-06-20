"""Step 5 接入层改造验收测试。

验证 data/etl/base.py + data/etl/loader.py：
- 22 个 Calculator 类全部可导入
- 每个类继承正确的中间基类（与 config/tushare_apis.json 的 incremental_strategy 一致）
- 每个类声明了 config_key / table_name / primary_keys
- CALCULATORS 注册表 22 个全齐
- fetch_tushare / get_pro_client / load_api_config 可导入
"""
import json
import sys
from pathlib import Path

# Mock pandas/tushare 避免环境依赖
sys.modules.setdefault("pandas", type(sys)("pandas"))
if not hasattr(sys.modules["pandas"], "DataFrame"):
    sys.modules["pandas"].DataFrame = object
    sys.modules["pandas"].concat = lambda *a, **k: None
    sys.modules["pandas"].NaT = None

print("=== Step 5: 接入层改造验收 ===")

# ===== 1. 导入 22 个 Calculator =====
print("\n=== 1. 导入 22 个 Calculator ===")
from data.etl.loader import CALCULATORS  # noqa: E402

assert len(CALCULATORS) == 22, f"CALCULATORS 应 22 个，实际 {len(CALCULATORS)}"
print(f"  OK  CALCULATORS 注册 {len(CALCULATORS)} 个")

# ===== 2. 加载 config/tushare_apis.json =====
print("\n=== 2. 加载 config/tushare_apis.json ===")
with open(Path("config/tushare_apis.json"), "r", encoding="utf-8") as f:
    apis = json.load(f)
assert len(apis) == 22
print(f"  OK  tushare_apis.json {len(apis)} 个接口")

# ===== 3. 每个类继承正确的中间基类 =====
print("\n=== 3. 继承链与 strategy 一致性 ===")
from data.etl.base import (  # noqa: E402
    TushareByAnnDateCalculator,
    TushareByTradeDateCalculator,
    TushareFullRefreshCalculator,
)

STRATEGY_BASE = {
    "by_trade_date": TushareByTradeDateCalculator,
    "by_ann_date": TushareByAnnDateCalculator,
    "full_refresh": TushareFullRefreshCalculator,
}

for key, cls in CALCULATORS.items():
    cfg = apis[cls.config_key]
    expected_base = STRATEGY_BASE[cfg["incremental_strategy"]]
    assert issubclass(cls, expected_base), (
        f"{key} strategy={cfg['incremental_strategy']} 应继承 {expected_base.__name__}，"
        f"实际 MRO: {[c.__name__ for c in cls.__mro__]}"
    )
print(f"  OK  22 个 Calculator 继承链与 strategy 全一致")

# ===== 4. 每个类声明 config_key / table_name / primary_keys =====
print("\n=== 4. 类属性完整性 ===")
for key, cls in CALCULATORS.items():
    assert cls.table_name, f"{cls.__name__} 未声明 table_name"
    assert cls.primary_keys, f"{cls.__name__} 未声明 primary_keys"
    cfg = apis[cls.config_key]
    # table_name 与 config 一致
    assert cls.table_name == cfg["table_name"], (
        f"{key} table_name 不一致：class={cls.table_name!r} config={cfg['table_name']!r}"
    )
print(f"  OK  22 个类 config_key/table_name/primary_keys 全齐且与 config 一致")

# ===== 5. write_mode 与 strategy 一致（继承自策略基类） =====
print("\n=== 5. write_mode 与 strategy 一致 ===")
for key, cls in CALCULATORS.items():
    cfg = apis[cls.config_key]
    expected_wm = "truncate" if cfg["incremental_strategy"] == "full_refresh" else "upsert"
    # write_mode 可能被类覆盖，但应与 config 一致
    assert cls.write_mode == expected_wm, (
        f"{key} write_mode={cls.write_mode!r} 应为 {expected_wm!r}"
    )
print(f"  OK  write_mode 全部与 strategy 一致")

# ===== 6. biz_date_col 与 strategy 一致 =====
print("\n=== 6. biz_date_col 与 strategy 一致 ===")
for key, cls in CALCULATORS.items():
    cfg = apis[cls.config_key]
    expected_bdc = cfg["biz_date_col"]
    assert cls.biz_date_col == expected_bdc, (
        f"{key} biz_date_col={cls.biz_date_col!r} 应为 {expected_bdc!r}"
    )
print(f"  OK  biz_date_col 全部与 config 一致")

# ===== 7. fetch_one_period 方法存在 =====
print("\n=== 7. fetch_one_period 方法存在 ===")
for key, cls in CALCULATORS.items():
    assert hasattr(cls, "fetch_one_period"), f"{cls.__name__} 缺 fetch_one_period"
    # 不应是 BaseIncremental 的抽象 raise（说明子类/中间基类已实现）
    import inspect
    src = inspect.getsource(cls.fetch_one_period)
    assert "NotImplementedError" not in src, f"{cls.__name__}.fetch_one_period 仍是抽象方法"
print(f"  OK  22 个类 fetch_one_period 全部已实现")

# ===== 8. base.py 关键函数可导入 =====
print("\n=== 8. base.py 关键函数可导入 ===")
from data.etl.base import (  # noqa: E402
    fetch_tushare,
    get_api_config,
    get_pro_client,
    load_api_config,
)

for fn in [fetch_tushare, get_api_config, get_pro_client, load_api_config]:
    assert callable(fn), f"{fn.__name__} 不可调用"
print(f"  OK  fetch_tushare / get_api_config / get_pro_client / load_api_config 全可导入")

# ===== 9. load_api_config 返回 22 个 =====
print("\n=== 9. load_api_config 返回 22 个 ===")
cfg_all = load_api_config()
assert len(cfg_all) == 22, f"load_api_config 应返回 22 个，实际 {len(cfg_all)}"
print(f"  OK  load_api_config 返回 {len(cfg_all)} 个")

# ===== 10. get_api_config 单个查询 =====
print("\n=== 10. get_api_config 单个查询 ===")
daily_cfg = get_api_config("daily")
assert daily_cfg["api_name"] == "daily"
assert daily_cfg["incremental_strategy"] == "by_trade_date"
print(f"  OK  get_api_config('daily') 正常")

# ===== 11. 特殊接口覆盖了 fetch_one_period =====
print("\n=== 11. 特殊接口覆盖 fetch_one_period ===")
special = {
    "trade_cal": "全量拉 2010 至今",
    "stock_basic": "遍历 list_status=L/D",
    "index_member_all": "遍历 is_new=Y/N",
    "index_daily": "遍历 index_codes",
    "index_dailybasic": "遍历 index_codes",
    "index_weight": "遍历 index_codes + 月份区间",
    "index_classify": "src=SW2021",
    "dividend": "逐 ann_date 单日调",
    "disclosure_date": "逐 ann_date 单日调",
}
for key, desc in special.items():
    cls = CALCULATORS[key]
    # 检查 fetch_one_period 是类自己定义的（不是继承中间基类的）
    own_methods = cls.__dict__.keys()
    assert "fetch_one_period" in own_methods, f"{cls.__name__} 应覆盖 fetch_one_period（{desc}）"
    print(f"  OK  {key}: {desc}")

print("\n=== 结果 ===")
print("ALL OK")
