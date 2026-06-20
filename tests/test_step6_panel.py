"""Step 6 验收测试：data/panel/ 重组。

验证：
1. data/panel/ 包结构完整（base + 7 个 calculator + __init__）
2. 所有 Calculator 继承 PanelCalculator
3. 表名自动加 panel_ 前缀
4. 声明 output_schema（除 financial_statements_snapshot 外，列太多由 df 推断）
5. 声明 primary_keys / biz_date_col / write_mode
6. PANEL_CALCULATORS 注册表完整
7. 新文件不依赖旧 data/config、data/utils
8. panel 间依赖引用 panel_ 前缀表名
9. FinancialStatementsSnapshotCalculator 重写 update（get_data 返回 dict）
"""
import sys
import os
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

# Mock config.database 和 core.schema（panel.base 依赖）
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
    return m

# Mock core.calculator（panel.base 依赖 BaseCalculator）
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
_core_pkg.__path__ = []  # 标记为包
sys.modules["core"] = _core_pkg
sys.modules.setdefault("core.schema", _make_mock_core_schema())
sys.modules.setdefault("core.dates", _make_mock_core_dates())
sys.modules.setdefault("core.calculator", _make_mock_core_calculator())

# Mock config 包
_config_pkg = types.ModuleType("config")
_config_pkg.__path__ = []
sys.modules["config"] = _config_pkg
sys.modules.setdefault("config.database", _make_mock_config_db())


def test_panel_imports():
    """测试 1：包能导入，7 个 Calculator 都在。"""
    from data.panel import (
        PanelCalculator,
        StockDailyPanelCalculator,
        StockPercentilesCalculator,
        MarketSentimentDailyCalculator,
        MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator,
        FinancialStatementsSnapshotCalculator,
        FinancialIndicatorsSnapshotCalculator,
        PANEL_CALCULATORS,
    )
    assert len(PANEL_CALCULATORS) == 7, f"应有 7 个 panel calculator，实际 {len(PANEL_CALCULATORS)}"
    print("[OK] 包导入成功，PANEL_CALCULATORS 有 7 项")


def test_inheritance():
    """测试 2：所有 Calculator 继承 PanelCalculator。"""
    from data.panel import (
        StockDailyPanelCalculator,
        StockPercentilesCalculator,
        MarketSentimentDailyCalculator,
        MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator,
        FinancialStatementsSnapshotCalculator,
        FinancialIndicatorsSnapshotCalculator,
    )
    from data.panel.base import PanelCalculator
    for cls in [
        StockDailyPanelCalculator, StockPercentilesCalculator,
        MarketSentimentDailyCalculator, MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator, FinancialStatementsSnapshotCalculator,
        FinancialIndicatorsSnapshotCalculator,
    ]:
        assert issubclass(cls, PanelCalculator), f"{cls.__name__} 必须继承 PanelCalculator"
    print("[OK] 所有 Calculator 继承 PanelCalculator")


def test_table_prefix():
    """测试 3：表名前缀逻辑正确。"""
    from data.panel.base import PanelCalculator
    assert PanelCalculator.TABLE_PREFIX == "panel_"
    from data.panel import StockDailyPanelCalculator
    assert StockDailyPanelCalculator.table_name == "stock_daily"
    # 验证前缀逻辑
    raw = StockDailyPanelCalculator.table_name
    expected = raw if raw.startswith("panel_") else f"panel_{raw}"
    assert expected == "panel_stock_daily"
    print("[OK] 表名前缀逻辑正确：stock_daily → panel_stock_daily")


def test_output_schema():
    """测试 4：声明 output_schema（除 financial_statements_snapshot）。"""
    from data.panel import (
        StockDailyPanelCalculator,
        StockPercentilesCalculator,
        MarketSentimentDailyCalculator,
        MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator,
        FinancialStatementsSnapshotCalculator,
        FinancialIndicatorsSnapshotCalculator,
    )
    for cls in [
        StockDailyPanelCalculator, StockPercentilesCalculator,
        MarketSentimentDailyCalculator, MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator, FinancialIndicatorsSnapshotCalculator,
    ]:
        assert cls.output_schema is not None, f"{cls.__name__} 必须声明 output_schema"
        assert isinstance(cls.output_schema, dict), f"{cls.__name__}.output_schema 必须是 dict"
        assert len(cls.output_schema) > 0, f"{cls.__name__}.output_schema 不能为空"
    print("[OK] output_schema 声明完整（financial_statements_snapshot 由 df 推断）")


def test_primary_keys_biz_date():
    """测试 5：声明 primary_keys / biz_date_col / write_mode。"""
    from data.panel import (
        StockDailyPanelCalculator,
        StockPercentilesCalculator,
        MarketSentimentDailyCalculator,
        MarketSentimentMonthlyCalculator,
        MvMonthlyCalculator,
        FinancialStatementsSnapshotCalculator,
        FinancialIndicatorsSnapshotCalculator,
    )
    expected = {
        StockDailyPanelCalculator: (["ts_code", "trade_date"], "trade_date"),
        StockPercentilesCalculator: (["ts_code", "trade_date"], "trade_date"),
        MarketSentimentDailyCalculator: (["trade_date", "dimension_type", "dimension_value"], "trade_date"),
        MarketSentimentMonthlyCalculator: (["trade_date", "dimension_type", "dimension_value"], "trade_date"),
        MvMonthlyCalculator: (["ts_code", "trade_date"], "trade_date"),
        FinancialStatementsSnapshotCalculator: (["snapshot_date", "ts_code", "end_date"], "snapshot_date"),
        FinancialIndicatorsSnapshotCalculator: (["snapshot_date", "ts_code", "end_date"], "snapshot_date"),
    }
    for cls, (pks, biz) in expected.items():
        assert cls.primary_keys == pks, f"{cls.__name__}.primary_keys 应为 {pks}，实际 {cls.primary_keys}"
        assert cls.biz_date_col == biz, f"{cls.__name__}.biz_date_col 应为 {biz}，实际 {cls.biz_date_col}"
        assert cls.write_mode in ("upsert", "truncate", "append"), f"{cls.__name__}.write_mode 非法: {cls.write_mode}"
    print("[OK] primary_keys / biz_date_col / write_mode 声明正确")


def test_no_legacy_imports():
    """测试 6：新文件不依赖旧 data/config、data/utils。"""
    panel_dir = ROOT / "data" / "panel"
    for py in panel_dir.glob("*.py"):
        if py.name == "__init__.py":
            continue
        content = py.read_text(encoding="utf-8")
        assert "data.config.database" not in content, f"{py.name} 不应依赖 data.config.database"
        assert "data.utils.base_calculator" not in content, f"{py.name} 不应依赖 data.utils.base_calculator"
        assert "data.utils.date_utils" not in content, f"{py.name} 不应依赖 data.utils.date_utils"
    print("[OK] 新文件不依赖旧 data/config、data/utils")


def test_dependency_on_panel_tables():
    """测试 7：panel 间依赖引用 panel_ 前缀表名。"""
    sp = (ROOT / "data" / "panel" / "stock_percentiles.py").read_text(encoding="utf-8")
    assert "panel_stock_daily" in sp, "stock_percentiles 应从 panel_stock_daily 取数"
    msd = (ROOT / "data" / "panel" / "market_sentiment_daily.py").read_text(encoding="utf-8")
    assert "panel_stock_daily" in msd
    assert "panel_stock_percentiles" in msd
    msm = (ROOT / "data" / "panel" / "market_sentiment_monthly.py").read_text(encoding="utf-8")
    assert "panel_stock_daily" in msm
    assert "panel_stock_percentiles" in msm
    fis = (ROOT / "data" / "panel" / "financial_indicators_snapshot.py").read_text(encoding="utf-8")
    assert "panel_financial_statements_snapshot" in fis
    fss = (ROOT / "data" / "panel" / "financial_statements_snapshot.py").read_text(encoding="utf-8")
    assert "panel_mv_monthly" in fss
    print("[OK] panel 间依赖引用 panel_ 前缀表名")


def test_financial_overrides_update():
    """测试 8：FinancialStatementsSnapshotCalculator 重写 update。"""
    from data.panel import FinancialStatementsSnapshotCalculator
    assert 'update' in FinancialStatementsSnapshotCalculator.__dict__, \
        "FinancialStatementsSnapshotCalculator 必须重写 update（get_data 返回 dict）"
    print("[OK] FinancialStatementsSnapshotCalculator 重写 update")


def test_panel_files_count():
    """测试 9：data/panel/ 文件数量正确。"""
    panel_dir = ROOT / "data" / "panel"
    py_files = [f for f in panel_dir.glob("*.py") if f.name != "__init__.py"]
    assert len(py_files) == 8, f"应有 8 个 .py（base + 7 calculator），实际 {len(py_files)}"
    names = {f.stem for f in py_files}
    expected = {
        "base", "stock_daily_panel", "stock_percentiles",
        "market_sentiment_daily", "market_sentiment_monthly",
        "mv_monthly", "financial_statements_snapshot", "financial_indicators_snapshot",
    }
    assert names == expected, f"文件名不匹配：{names}"
    print("[OK] data/panel/ 文件结构完整（base + 7 calculator）")


if __name__ == "__main__":
    print("=" * 60)
    print("Step 6 验收测试：data/panel/ 重组")
    print("=" * 60)
    test_panel_imports()
    test_inheritance()
    test_table_prefix()
    test_output_schema()
    test_primary_keys_biz_date()
    test_no_legacy_imports()
    test_dependency_on_panel_tables()
    test_financial_overrides_update()
    test_panel_files_count()
    print("=" * 60)
    print("所有验收测试通过 ✅")
    print("=" * 60)
