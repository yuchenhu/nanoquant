"""Step 2 import 链冒烟测试（mock 掉第三方库，只验证项目内模块解析）。"""
import sys
import types
from unittest.mock import MagicMock

# ===== Mock 第三方库（Python 3.14 环境装不了 pandas C 扩展） =====
for mod_name in [
    "pandas", "numpy", "sqlalchemy", "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.insert", "pymysql", "dotenv", "statsmodels",
    "statsmodels.api",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# sqlalchemy 子模块需要真实属性
import sqlalchemy  # mocked
sqlalchemy.create_engine = MagicMock(return_value=MagicMock())
sqlalchemy.inspect = MagicMock()
sqlalchemy.text = MagicMock()
sqlalchemy.MetaData = MagicMock()
sqlalchemy.Table = MagicMock()

# ===== 实际 import 项目模块 =====
failures = []
modules_to_test = [
    "config",
    "config.settings",
    "config.database",
    "core",
    "core.dates",
    "core.preprocessing",
    "core.schema",
    "core.calculator",
    # shim
    "data.config.database",
    "data.utils.date_utils",
    "data.utils.preprocessing",
]
for mod in modules_to_test:
    try:
        __import__(mod)
        print(f"  OK  {mod}")
    except Exception as e:
        failures.append((mod, e))
        print(f"  FAIL {mod}: {type(e).__name__}: {e}")

# ===== 验证 shim 暴露的名字 =====
print("\n=== shim 名字检查 ===")
from data.config import database as db_shim
for name in ["engine", "execute_sql", "upsert_data", "save_to_database", "text",
             "get_table_schema", "DB_CONFIG", "TABLE_SCHEMAS"]:
    assert hasattr(db_shim, name), f"data.config.database 缺 {name}"
    print(f"  OK  data.config.database.{name}")

from data.utils import date_utils as du_shim
for name in ["get_today_str", "is_trading_day", "get_previous_n_trading_date",
             "get_next_n_trading_date", "get_recent_weekday", "get_recent_month",
             "get_recent_quarter_dates", "get_month_start_end"]:
    assert hasattr(du_shim, name), f"data.utils.date_utils 缺 {name}"
    print(f"  OK  data.utils.date_utils.{name}")

from data.utils import preprocessing as pp_shim
for name in ["mad_winsorize", "standardize_factor", "quantile_factor",
             "rank_factor", "neutralize_factor", "orthogonalize_factor"]:
    assert hasattr(pp_shim, name), f"data.utils.preprocessing 缺 {name}"
    print(f"  OK  data.utils.preprocessing.{name}")

# ===== 验证新 BaseCalculator 类属性 =====
print("\n=== core.calculator.BaseCalculator ===")
from core.calculator import BaseCalculator
assert hasattr(BaseCalculator, "update"), "BaseCalculator 缺 update"
assert hasattr(BaseCalculator, "save_to_database"), "BaseCalculator 缺 save_to_database"
assert hasattr(BaseCalculator, "get_data"), "BaseCalculator 缺 get_data"
assert hasattr(BaseCalculator, "process_data"), "BaseCalculator 缺 process_data"
assert hasattr(BaseCalculator, "_set_biz_date"), "BaseCalculator 缺 _set_biz_date"
assert hasattr(BaseCalculator, "_get_biz_date"), "BaseCalculator 缺 _get_biz_date"
assert BaseCalculator.biz_date_col == "trade_date", "默认 biz_date_col 应为 trade_date"
print("  OK  BaseCalculator.update / save_to_database / get_data / process_data")
print("  OK  BaseCalculator._set_biz_date / _get_biz_date（水位表）")
print(f"  OK  BaseCalculator.biz_date_col 默认 = {BaseCalculator.biz_date_col!r}")

# ===== 验证 schema 工具 =====
print("\n=== core.schema ===")
from core.schema import infer_schema_from_df, generate_create_table_sql, ensure_table, evolve_schema, convert_date_columns
print("  OK  infer_schema_from_df / generate_create_table_sql / ensure_table / evolve_schema / convert_date_columns")

print("\n=== 结果 ===")
if failures:
    print(f"FAILED: {len(failures)} 个模块 import 失败")
    sys.exit(1)
else:
    print("ALL OK")
