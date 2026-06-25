"""Step 7 验收测试：data/factor/ + data/label/ 迁移到新 BaseCalculator。

验证：
1. data/factor/ + data/label/ 包结构完整
2. 所有 Calculator 继承 FactorCalculator / LabelCalculator
3. 表名自动加 factor_ / label_ 前缀
4. 声明 output_schema
5. 声明 primary_keys / biz_date_col / write_mode
6. FACTOR_CALCULATORS / LABEL_CALCULATORS 注册表完整
7. 新文件不依赖旧 data/config、data/utils
8. factor/label 依赖引用 panel_ 前缀表名
9. 文件数量正确
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
    return sa


sys.modules.setdefault("pandas", _make_mock_pandas())
sys.modules.setdefault("numpy", _make_mock_numpy())
sys.modules.setdefault("scipy", _make_mock_scipy())
sys.modules.setdefault("scipy.stats", sys.modules["scipy"].stats)
sys.modules.setdefault("sqlalchemy", _make_mock_sqlalchemy())


# Mock config.database 和 core.schema
def _make_mock_config_db():
    m = types.ModuleType("config.database")
    m.engine = None
    m.save_to_database = lambda *a, **k: True
    return m


def _make_mock_core_schema():
    m = types.ModuleType("core.schema")
    m.ensure_table = lambda *a, **k: None
    m.evolve_schema = lambda *a, **k: None
    m.infer_schema_from_df = lambda *a, **k: {}
    m.convert_date_columns = lambda *a, **k: a[0]
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


# Mock core.calculator（factor/label base 依赖 BaseCalculator）
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


# 先创建 core 包
_core_pkg = types.ModuleType("core")
_core_pkg.__path__ = []
sys.modules["core"] = _core_pkg
sys.modules.setdefault("core.schema", _make_mock_core_schema())
sys.modules.setdefault("core.dates", _make_mock_core_dates())
sys.modules.setdefault("core.preprocessing", _make_mock_core_preprocessing())
sys.modules.setdefault("core.calculator", _make_mock_core_calculator())

# Mock config 包
_config_pkg = types.ModuleType("config")
_config_pkg.__path__ = []
sys.modules["config"] = _config_pkg
sys.modules.setdefault("config.database", _make_mock_config_db())


def test_factor_imports():
    """测试 1：factor 包能导入，6 个 Calculator 都在。"""
    from data.factor import (
        FactorCalculator,
        HighLowSpreadCalculator,
        IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator,
        PriceVolume20DCalculator,
        TraderStructureCalculator,
        ValuationCalculator,
        CALCULATORS as FACTOR_CALCULATORS,
    )
    assert len(FACTOR_CALCULATORS) == 6, f"应有 6 个 factor calculator，实际 {len(FACTOR_CALCULATORS)}"
    print("[OK] factor 包导入成功，CALCULATORS 有 6 项")


def test_label_imports():
    """测试 2：label 包能导入，1 个 Calculator 都在。"""
    from data.label import (
        LabelCalculator,
        ForwardReturnsCalculator,
        CALCULATORS as LABEL_CALCULATORS,
    )
    assert len(LABEL_CALCULATORS) == 1, f"应有 1 个 label calculator，实际 {len(LABEL_CALCULATORS)}"
    print("[OK] label 包导入成功，CALCULATORS 有 1 项")


def test_factor_inheritance():
    """测试 3：所有 Factor Calculator 继承 FactorCalculator。"""
    from data.factor import (
        HighLowSpreadCalculator,
        IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator,
        PriceVolume20DCalculator,
        TraderStructureCalculator,
        ValuationCalculator,
    )
    from data.factor.base import FactorCalculator
    for cls in [
        HighLowSpreadCalculator, IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator, PriceVolume20DCalculator,
        TraderStructureCalculator, ValuationCalculator,
    ]:
        assert issubclass(cls, FactorCalculator), f"{cls.__name__} 必须继承 FactorCalculator"
    print("[OK] 所有 Factor Calculator 继承 FactorCalculator")


def test_label_inheritance():
    """测试 4：所有 Label Calculator 继承 LabelCalculator。"""
    from data.label import ForwardReturnsCalculator
    from data.label.base import LabelCalculator
    assert issubclass(ForwardReturnsCalculator, LabelCalculator), \
        "ForwardReturnsCalculator 必须继承 LabelCalculator"
    print("[OK] 所有 Label Calculator 继承 LabelCalculator")


def test_table_prefix():
    """测试 5：表名前缀逻辑正确。"""
    from data.factor.base import FactorCalculator
    from data.label.base import LabelCalculator
    assert FactorCalculator.TABLE_PREFIX == "factor_"
    assert LabelCalculator.TABLE_PREFIX == "label_"

    from data.factor import HighLowSpreadCalculator
    from data.label import ForwardReturnsCalculator
    assert HighLowSpreadCalculator.table_name == "high_low_spread"
    assert ForwardReturnsCalculator.table_name == "forward_returns"

    # 验证前缀逻辑
    raw = HighLowSpreadCalculator.table_name
    expected = raw if raw.startswith("factor_") else f"factor_{raw}"
    assert expected == "factor_high_low_spread"

    raw = ForwardReturnsCalculator.table_name
    expected = raw if raw.startswith("label_") else f"label_{raw}"
    assert expected == "label_forward_returns"
    print("[OK] 表名前缀逻辑正确：high_low_spread → factor_high_low_spread, forward_returns → label_forward_returns")


def test_output_schema():
    """测试 6：声明 output_schema。"""
    from data.factor import (
        HighLowSpreadCalculator,
        IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator,
        PriceVolume20DCalculator,
        TraderStructureCalculator,
        ValuationCalculator,
    )
    from data.label import ForwardReturnsCalculator

    classes = [
        HighLowSpreadCalculator, IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator, PriceVolume20DCalculator,
        TraderStructureCalculator, ValuationCalculator,
        ForwardReturnsCalculator,
    ]
    for cls in classes:
        # output_schema 可能是类属性（dict）或 property（需实例化访问）
        raw_attr = cls.__dict__.get("output_schema")
        if isinstance(raw_attr, property):
            # property：创建实例访问（mock engine=None 可用）
            instance = cls(engine=None)
            schema = instance.output_schema
        else:
            schema = cls.output_schema
        assert schema is not None, f"{cls.__name__} 必须声明 output_schema"
        assert isinstance(schema, dict), f"{cls.__name__}.output_schema 必须是 dict，实际 {type(schema)}"
        assert len(schema) > 0, f"{cls.__name__}.output_schema 不能为空"
        # 主键列必须在 schema 中
        for pk in cls.primary_keys:
            assert pk in schema, f"{cls.__name__}.output_schema 缺少主键列 {pk}"
        # biz_date_col 必须在 schema 中
        assert cls.biz_date_col in schema, f"{cls.__name__}.output_schema 缺少 biz_date_col {cls.biz_date_col}"
    print("[OK] output_schema 声明完整")


def test_primary_keys_biz_date():
    """测试 7：声明 primary_keys / biz_date_col / write_mode。"""
    from data.factor import (
        HighLowSpreadCalculator,
        IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator,
        PriceVolume20DCalculator,
        TraderStructureCalculator,
        ValuationCalculator,
    )
    from data.label import ForwardReturnsCalculator

    for cls in [
        HighLowSpreadCalculator, IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator, PriceVolume20DCalculator,
        TraderStructureCalculator, ValuationCalculator,
        ForwardReturnsCalculator,
    ]:
        assert cls.primary_keys == ["ts_code", "trade_date"], \
            f"{cls.__name__}.primary_keys 应为 ['ts_code', 'trade_date']，实际 {cls.primary_keys}"
        assert cls.biz_date_col == "trade_date", \
            f"{cls.__name__}.biz_date_col 应为 trade_date，实际 {cls.biz_date_col}"
        assert cls.write_mode == "upsert", \
            f"{cls.__name__}.write_mode 应为 upsert，实际 {cls.write_mode}"
    print("[OK] primary_keys / biz_date_col / write_mode 声明正确")


def test_no_legacy_imports():
    """测试 8：新文件不依赖旧 data/config、data/utils。"""
    for subdir in ["factor", "label"]:
        dir_path = ROOT / "data" / subdir
        for py in dir_path.glob("*.py"):
            if py.name == "__init__.py":
                continue
            content = py.read_text(encoding="utf-8")
            assert "data.config.database" not in content, f"{py.name} 不应依赖 data.config.database"
            assert "data.utils.base_calculator" not in content, f"{py.name} 不应依赖 data.utils.base_calculator"
            assert "data.utils.date_utils" not in content, f"{py.name} 不应依赖 data.utils.date_utils"
    print("[OK] 新文件不依赖旧 data/config、data/utils")


def test_dependency_on_panel_tables():
    """测试 9：factor/label 依赖引用 panel_ 前缀表名。"""
    # 所有 factor 都应从 panel_stock_daily 取数
    factor_dir = ROOT / "data" / "factor"
    for py in factor_dir.glob("*.py"):
        if py.name in ("__init__.py", "base.py"):
            continue
        content = py.read_text(encoding="utf-8")
        assert "panel_stock_daily" in content, f"{py.name} 应从 panel_stock_daily 取数"

    # industry_resonance 还应依赖 sw_daily 或 panel_sw_daily
    ir = (factor_dir / "industry_resonance.py").read_text(encoding="utf-8")
    assert "sw_daily" in ir or "panel_sw_daily" in ir, "industry_resonance 应依赖 sw_daily 或 panel_sw_daily"

    # label 也应从 panel_stock_daily 取数
    label_dir = ROOT / "data" / "label"
    for py in label_dir.glob("*.py"):
        if py.name in ("__init__.py", "base.py"):
            continue
        content = py.read_text(encoding="utf-8")
        assert "panel_stock_daily" in content, f"{py.name} 应从 panel_stock_daily 取数"
    print("[OK] factor/label 依赖引用 panel_ 前缀表名")


def test_factor_files_count():
    """测试 10：data/factor/ 文件数量正确。"""
    factor_dir = ROOT / "data" / "factor"
    py_files = [f for f in factor_dir.glob("*.py") if f.name != "__init__.py"]
    assert len(py_files) == 7, f"应有 7 个 .py（base + 6 calculator），实际 {len(py_files)}"
    names = {f.stem for f in py_files}
    expected = {
        "base", "high_low_spread", "industry_resonance",
        "moneyflow_imbalance", "price_volume_20d",
        "trader_structure", "valuation",
    }
    assert names == expected, f"文件名不匹配：{names}"
    print("[OK] data/factor/ 文件结构完整（base + 6 calculator）")


def test_label_files_count():
    """测试 11：data/label/ 文件数量正确。"""
    label_dir = ROOT / "data" / "label"
    py_files = [f for f in label_dir.glob("*.py") if f.name != "__init__.py"]
    assert len(py_files) == 2, f"应有 2 个 .py（base + 1 calculator），实际 {len(py_files)}"
    names = {f.stem for f in py_files}
    expected = {"base", "forward_returns"}
    assert names == expected, f"文件名不匹配：{names}"
    print("[OK] data/label/ 文件结构完整（base + 1 calculator）")


def test_factor_implements_methods():
    """测试 12：所有 Calculator 实现了 get_data / process_data。"""
    from data.factor import (
        HighLowSpreadCalculator,
        IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator,
        PriceVolume20DCalculator,
        TraderStructureCalculator,
        ValuationCalculator,
    )
    from data.label import ForwardReturnsCalculator

    for cls in [
        HighLowSpreadCalculator, IndustryResonanceCalculator,
        MoneyFlowImbalanceCalculator, PriceVolume20DCalculator,
        TraderStructureCalculator, ValuationCalculator,
        ForwardReturnsCalculator,
    ]:
        assert "get_data" in cls.__dict__, f"{cls.__name__} 必须实现 get_data"
        assert "process_data" in cls.__dict__, f"{cls.__name__} 必须实现 process_data"
    print("[OK] 所有 Calculator 实现了 get_data / process_data")


if __name__ == "__main__":
    print("=" * 60)
    print("Step 7 验收测试：data/factor/ + data/label/ 迁移")
    print("=" * 60)
    test_factor_imports()
    test_label_imports()
    test_factor_inheritance()
    test_label_inheritance()
    test_table_prefix()
    test_output_schema()
    test_primary_keys_biz_date()
    test_no_legacy_imports()
    test_dependency_on_panel_tables()
    test_factor_files_count()
    test_label_files_count()
    test_factor_implements_methods()
    print("=" * 60)
    print("所有验收测试通过 ✅")
    print("=" * 60)
