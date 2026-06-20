"""Step 8 验收测试：scripts/00_init_database.py + run_ingest.py + run_compute.py。

验证：
1. 三个脚本文件存在
2. 00_init_database.py 有 main() / test_connection() / create_meta_tables() / init_all_tables()
3. run_ingest.py 有 main() / list_calculators() / run_ingest()
4. run_compute.py 有 main() / list_calculators() / run_compute()
5. 三个脚本都支持 --help（argparse 配置正确）
6. 三个脚本 sys.path 注入项目根目录
7. 00_init_database.py 收集所有层 Calculator（etl/panel/factor/label）
8. run_ingest.py 引用 data.etl.loader.CALCULATORS
9. run_compute.py 引用 panel/factor/label 三层注册表
10. 脚本不依赖旧 data/config、data/utils
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ===== Mock 重依赖模块（环境 pandas 在 Python 3.14 下有问题） =====
def _make_mock_pandas():
    pd = types.ModuleType("pandas")
    pd.DataFrame = type("DataFrame", (), {})
    pd.Series = type("Series", (), {})
    pd.read_sql = lambda *a, **k: pd.DataFrame()
    pd.concat = lambda *a, **k: pd.DataFrame()
    pd.merge = lambda *a, **k: pd.DataFrame()
    pd.merge_asof = lambda *a, **k: pd.DataFrame()
    pd.to_datetime = lambda *a, **k: None
    pd.NaT = None
    pd.DateOffset = type("DateOffset", (), {})
    pd.date_range = lambda *a, **k: []
    return pd


def _make_mock_numpy():
    np = types.ModuleType("numpy")
    np.nan = float("nan")
    np.inf = float("inf")
    np.select = lambda *a, **k: None
    np.log = lambda *a, **k: None
    np.exp = lambda *a, **k: None
    np.arange = lambda *a, **k: []
    np.power = lambda *a, **k: None
    np.abs = lambda *a, **k: None
    np.sqrt = lambda *a, **k: None
    return np


def _make_mock_scipy():
    scipy = types.ModuleType("scipy")
    stats = types.ModuleType("scipy.stats")
    stats.percentileofscore = lambda *a, **k: 0.0
    scipy.stats = stats
    return scipy


def _make_mock_sqlalchemy():
    sa = types.ModuleType("sqlalchemy")
    sa.text = lambda *a, **k: None
    sa.inspect = lambda *a, **k: None
    sa.create_engine = lambda *a, **k: None
    sa.MetaData = type("MetaData", (), {})
    sa.Table = type("Table", (), {})
    return sa


sys.modules.setdefault("pandas", _make_mock_pandas())
sys.modules.setdefault("numpy", _make_mock_numpy())
sys.modules.setdefault("scipy", _make_mock_scipy())
sys.modules.setdefault("scipy.stats", sys.modules["scipy"].stats)
sys.modules.setdefault("sqlalchemy", _make_mock_sqlalchemy())


# Mock config 包
_config_pkg = types.ModuleType("config")
_config_pkg.__path__ = []
sys.modules["config"] = _config_pkg


def _make_mock_config_db():
    m = types.ModuleType("config.database")
    m.engine = None
    m.save_to_database = lambda *a, **k: True
    m.execute_sql = lambda *a, **k: None
    return m


sys.modules.setdefault("config.database", _make_mock_config_db())


def _make_mock_config_settings():
    m = types.ModuleType("config.settings")

    class _S:
        db_url = "sqlite://"
        db_host = "localhost"
        db_port = 3306
        db_user = "u"
        db_password = "p"
        db_database = "test"
        db_charset = "utf8mb4"
        tushare_token = "test_token"

    m.settings = _S()
    return m


sys.modules.setdefault("config.settings", _make_mock_config_settings())


# Mock core 包
_core_pkg = types.ModuleType("core")
_core_pkg.__path__ = []
sys.modules["core"] = _core_pkg


def _make_mock_core_schema():
    m = types.ModuleType("core.schema")
    m.ensure_table = lambda *a, **k: None
    m.evolve_schema = lambda *a, **k: None
    m.infer_schema_from_df = lambda *a, **k: {}
    m.convert_date_columns = lambda *a, **k: a[0]
    m.generate_create_table_sql = lambda *a, **k: "CREATE TABLE ..."
    m.table_exists = lambda *a, **k: False
    m.get_existing_columns = lambda *a, **k: {}
    return m


def _make_mock_core_dates():
    m = types.ModuleType("core.dates")
    m.get_today_str = lambda: "20260101"
    m.get_previous_n_trading_date = lambda *a, **k: "20200101"
    m.get_next_n_trading_date = lambda *a, **k: "20200101"
    return m


def _make_mock_core_preprocessing():
    m = types.ModuleType("core.preprocessing")
    m.mad_winsorize = lambda *a, **k: None
    m.neutralize_factor = lambda *a, **k: None
    m.orthogonalize_factor = lambda *a, **k: None
    m.rank_factor = lambda *a, **k: None
    return m


def _make_mock_core_calculator():
    m = types.ModuleType("core.calculator")

    class BaseCalculator:
        table_name = ""
        biz_date_col = "trade_date"
        primary_keys = []
        write_mode = "upsert"
        output_schema = None
        type_overrides = None

        def __init__(self, engine=None):
            self.engine = engine
            import logging
            self.logger = logging.getLogger(self.__class__.__name__)

        def update(self, *a, **k):
            return None

        def save_to_database(self, *a, **k):
            return None

        def _normalize_date(self, d):
            return (d or "").replace("-", "")

        def _next_after_biz_date(self):
            return ""

        def _max_biz_date(self, df):
            return None

        def _set_biz_date(self, *a, **k):
            return None

    m.BaseCalculator = BaseCalculator
    return m


sys.modules.setdefault("core.schema", _make_mock_core_schema())
sys.modules.setdefault("core.dates", _make_mock_core_dates())
sys.modules.setdefault("core.preprocessing", _make_mock_core_preprocessing())
sys.modules.setdefault("core.calculator", _make_mock_core_calculator())


# Mock pipeline 包（data.etl.base 依赖）
_pipeline_pkg = types.ModuleType("pipeline")
_pipeline_pkg.__path__ = []
sys.modules["pipeline"] = _pipeline_pkg

_incremental_pkg = types.ModuleType("pipeline.incremental")
_incremental_pkg.__path__ = []
sys.modules["pipeline.incremental"] = _incremental_pkg


def _make_mock_by_trade_date():
    m = types.ModuleType("pipeline.incremental.by_trade_date")

    class ByTradeDateCalculator:
        table_name = ""
        biz_date_col = "trade_date"
        primary_keys = []
        write_mode = "upsert"
        output_schema = None

        def __init__(self, engine=None):
            self.engine = engine

        def update(self, *a, **k):
            return None

    m.ByTradeDateCalculator = ByTradeDateCalculator
    return m


def _make_mock_by_ann_date():
    m = types.ModuleType("pipeline.incremental.by_ann_date")

    class ByAnnDateCalculator:
        table_name = ""
        biz_date_col = "ann_date"
        primary_keys = []
        write_mode = "upsert"
        output_schema = None

        def __init__(self, engine=None):
            self.engine = engine

        def update(self, *a, **k):
            return None

    m.ByAnnDateCalculator = ByAnnDateCalculator
    return m


def _make_mock_full_refresh():
    m = types.ModuleType("pipeline.incremental.full_refresh")

    class FullRefreshCalculator:
        table_name = ""
        primary_keys = []
        write_mode = "truncate"
        output_schema = None

        def __init__(self, engine=None):
            self.engine = engine

        def update(self, *a, **k):
            return None

    m.FullRefreshCalculator = FullRefreshCalculator
    return m


sys.modules.setdefault("pipeline.incremental.by_trade_date", _make_mock_by_trade_date())
sys.modules.setdefault("pipeline.incremental.by_ann_date", _make_mock_by_ann_date())
sys.modules.setdefault("pipeline.incremental.full_refresh", _make_mock_full_refresh())


def test_scripts_exist():
    """测试 1：三个脚本文件存在。"""
    assert (ROOT / "scripts" / "00_init_database.py").exists()
    assert (ROOT / "scripts" / "run_ingest.py").exists()
    assert (ROOT / "scripts" / "run_compute.py").exists()
    print("[OK] 三个脚本文件存在")


def test_init_database_functions():
    """测试 2：00_init_database.py 有必要函数。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "init_db", ROOT / "scripts" / "00_init_database.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "test_connection")
    assert hasattr(mod, "create_meta_tables")
    assert hasattr(mod, "init_all_tables")
    assert hasattr(mod, "_collect_calculators")
    print("[OK] 00_init_database.py 函数完整：main / test_connection / create_meta_tables / init_all_tables / _collect_calculators")


def test_run_ingest_functions():
    """测试 3：run_ingest.py 有必要函数。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_ingest", ROOT / "scripts" / "run_ingest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "list_calculators")
    assert hasattr(mod, "run_ingest")
    print("[OK] run_ingest.py 函数完整：main / list_calculators / run_ingest")


def test_run_compute_functions():
    """测试 4：run_compute.py 有必要函数。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_compute", ROOT / "scripts" / "run_compute.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "list_calculators")
    assert hasattr(mod, "run_compute")
    assert hasattr(mod, "_load_layer_registries")
    print("[OK] run_compute.py 函数完整：main / list_calculators / run_compute / _load_layer_registries")


def test_argparse_config():
    """测试 5：三个脚本都配置了 argparse。"""
    for script_name in ["00_init_database.py", "run_ingest.py", "run_compute.py"]:
        content = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "argparse.ArgumentParser" in content, f"{script_name} 应使用 argparse"
        assert "add_argument" in content, f"{script_name} 应添加参数"
        assert "if __name__" in content, f"{script_name} 应有 __main__ 入口"
    print("[OK] 三个脚本都配置了 argparse")


def test_sys_path_injection():
    """测试 6：三个脚本都注入项目根目录到 sys.path。"""
    for script_name in ["00_init_database.py", "run_ingest.py", "run_compute.py"]:
        content = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "sys.path.insert" in content, f"{script_name} 应注入 sys.path"
        assert "ROOT" in content, f"{script_name} 应定义 ROOT"
    print("[OK] 三个脚本都注入项目根目录到 sys.path")


def test_init_database_collects_all_layers():
    """测试 7：00_init_database.py 收集所有层 Calculator。"""
    content = (ROOT / "scripts" / "00_init_database.py").read_text(encoding="utf-8")
    assert "data.etl.loader" in content, "应引用 data.etl.loader"
    assert "data.panel" in content, "应引用 data.panel"
    assert "data.factor" in content, "应引用 data.factor"
    assert "data.label" in content, "应引用 data.label"
    assert "CALCULATORS" in content or "PANEL_CALCULATORS" in content
    assert "etl_biz_date" in content, "应创建 etl_biz_date 水位表"
    assert "etl_schema_log" in content, "应创建 etl_schema_log 留痕表"
    print("[OK] 00_init_database.py 收集 etl/panel/factor/label 四层 Calculator")


def test_run_ingest_uses_etl_registry():
    """测试 8：run_ingest.py 引用 data.etl.loader.CALCULATORS。"""
    content = (ROOT / "scripts" / "run_ingest.py").read_text(encoding="utf-8")
    assert "from data.etl.loader import CALCULATORS" in content, \
        "应 from data.etl.loader import CALCULATORS"
    assert "instance.update" in content, "应调用 instance.update()"
    print("[OK] run_ingest.py 引用 data.etl.loader.CALCULATORS")


def test_run_compute_uses_three_layer_registries():
    """测试 9：run_compute.py 引用 panel/factor/label 三层注册表。"""
    content = (ROOT / "scripts" / "run_compute.py").read_text(encoding="utf-8")
    assert "from data.panel import PANEL_CALCULATORS" in content, \
        "应 from data.panel import PANEL_CALCULATORS"
    assert "from data.factor import CALCULATORS as FACTOR_CALCULATORS" in content, \
        "应 from data.factor import CALCULATORS as FACTOR_CALCULATORS"
    assert "from data.label import CALCULATORS as LABEL_CALCULATORS" in content, \
        "应 from data.label import CALCULATORS as LABEL_CALCULATORS"
    assert "panel" in content and "factor" in content and "label" in content
    print("[OK] run_compute.py 引用 panel/factor/label 三层注册表")


def test_no_legacy_imports():
    """测试 10：脚本不依赖旧 data/config、data/utils。"""
    for script_name in ["00_init_database.py", "run_ingest.py", "run_compute.py"]:
        content = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "data.config.database" not in content, f"{script_name} 不应依赖 data.config.database"
        assert "data.utils.base_calculator" not in content, f"{script_name} 不应依赖 data.utils.base_calculator"
        assert "data.utils.date_utils" not in content, f"{script_name} 不应依赖 data.utils.date_utils"
    print("[OK] 脚本不依赖旧 data/config、data/utils")


def test_run_compute_layer_order():
    """测试 11：run_compute.py 默认按 panel → factor → label 顺序。"""
    content = (ROOT / "scripts" / "run_compute.py").read_text(encoding="utf-8")
    # 默认顺序应为 panel, factor, label
    panel_pos = content.find('"panel"')
    factor_pos = content.find('"factor"')
    label_pos = content.find('"label"')
    assert 0 < panel_pos < factor_pos < label_pos, \
        "默认 layer 顺序应为 panel → factor → label"
    print("[OK] run_compute.py 默认按 panel → factor → label 顺序")


def test_init_database_dry_run():
    """测试 12：00_init_database.py 支持 --dry-run。"""
    content = (ROOT / "scripts" / "00_init_database.py").read_text(encoding="utf-8")
    assert "--dry-run" in content, "应支持 --dry-run 参数"
    assert "dry_run" in content, "应有 dry_run 参数处理逻辑"
    print("[OK] 00_init_database.py 支持 --dry-run")


if __name__ == "__main__":
    print("=" * 60)
    print("Step 8 验收测试：scripts/ 初始化 + 运行脚本")
    print("=" * 60)
    test_scripts_exist()
    test_init_database_functions()
    test_run_ingest_functions()
    test_run_compute_functions()
    test_argparse_config()
    test_sys_path_injection()
    test_init_database_collects_all_layers()
    test_run_ingest_uses_etl_registry()
    test_run_compute_uses_three_layer_registries()
    test_no_legacy_imports()
    test_run_compute_layer_order()
    test_init_database_dry_run()
    print("=" * 60)
    print("所有验收测试通过 ✅")
    print("=" * 60)
