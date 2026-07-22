"""Step 10 验收测试：portfolio/ + backtest/ + signals/（ETF 截面轮动 MVP 闭环）。

验证：
1. portfolio/ 包结构完整（__init__.py, universe.py, strategy.py）
2. backtest/ 包结构完整（__init__.py, metrics.py, engine.py)
3. signals/ 包结构完整（__init__.py, generator.py）
4. portfolio.strategy.CrossSectionalMomentumStrategy 有 compute_target_weights / apply_constraints
5. backtest.engine.VectorizedBacktester 有 run 方法
6. backtest.metrics.compute_metrics 计算正确
7. signals.generator.SignalGenerator 有 generate / save 方法
8. signals 复用 portfolio.strategy（不重复实现策略逻辑）
9. backtest 复用 portfolio.strategy（engine 调用 strategy.compute_target_weights）
10. DEFAULT_ETF_POOL 非空，含宽基/行业/风格三类
11. 表名前缀：signal_rebalance（signals 层）
12. 三层不依赖旧 data/config、data/utils

已废弃（2026-07-22）：signal_rebalance 接口删除（write_mode=upsert 违反项目规则），
signals/generator.py 已删除，本测试全部 skip。后续如需调仓信号应在 portfolio/ 层重写。
"""
import pytest

pytestmark = pytest.mark.skip(reason="signal_rebalance 接口已删除，signals/generator.py 已废弃")

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ===== Mock 重依赖模块 =====
def _make_mock_pandas():
    pd = types.ModuleType("pandas")

    class _MockSeries:
        """轻量 Series mock，支持 compute_metrics 用到的操作。"""
        def __init__(self, data=None, *a, **k):
            if data is None:
                self._data = []
            elif isinstance(data, (list, tuple)):
                self._data = list(data)
            else:
                self._data = [data]
            self.index = list(range(len(self._data)))

        def __len__(self):
            return len(self._data)

        def __getitem__(self, i):
            return _MockSeries([self._data[i]]) if isinstance(i, slice) else self._data[i]

        def __iter__(self):
            return iter(self._data)

        @property
        def iloc(self):
            class _Iloc:
                def __getitem__(self, i):
                    return self_._data[i]
            self_ = self
            return _Iloc()

        def cumprod(self):
            r = []
            acc = 1.0
            for v in self._data:
                acc *= (1 + v) if False else v
                r.append(acc)
            # compute_metrics 里是 (1+returns).cumprod()，这里 data 已是 1+ret
            return _MockSeries(r)

        def cummax(self):
            r = []
            m = None
            for v in self._data:
                m = v if m is None else max(m, v)
                r.append(m)
            return _MockSeries(r)

        def std(self):
            if len(self._data) < 2:
                return 0.0
            mean = sum(self._data) / len(self._data)
            var = sum((x - mean) ** 2 for x in self._data) / (len(self._data) - 1)
            return var ** 0.5

        def __gt__(self, val):
            return _MockSeries([1 if x > val else 0 for x in self._data])

        def sum(self):
            return sum(self._data)

        def dropna(self):
            return _MockSeries([x for x in self._data if x is not None])

    pd.Series = _MockSeries
    pd.DataFrame = type("DataFrame", (), {
        "__init__": lambda self, *a, **k: None,
        "empty": True,
        "set_index": lambda self, *a, **k: self,
        "pivot": lambda self, *a, **k: self,
        "pct_change": lambda self, *a, **k: self,
        "rolling": lambda self, *a, **k: self,
        "std": lambda self, *a, **k: self,
        "iloc": type("iloc", (), {"__getitem__": lambda self, i: None})(),
        "cumprod": lambda self, *a, **k: self,
        "cummax": lambda self, *a, **k: self,
        "dropna": lambda self, *a, **k: self,
        "sort_values": lambda self, *a, **k: self,
        "head": lambda self, *a, **k: self,
        "index": [],
        "tolist": lambda self: [],
        "columns": [],
    })
    pd.read_sql = lambda *a, **k: pd.DataFrame()
    pd.concat = lambda *a, **k: pd.DataFrame()
    pd.merge = lambda *a, **k: pd.DataFrame()
    pd.to_datetime = lambda *a, **k: None
    pd.Timestamp = type("Timestamp", (), {
        "week": 1, "month": 1,
    })
    pd.NaT = None
    return pd


def _make_mock_numpy():
    np = types.ModuleType("numpy")
    np.nan = float("nan")
    np.sqrt = lambda x: x ** 0.5
    return np


sys.modules.setdefault("pandas", _make_mock_pandas())
sys.modules.setdefault("numpy", _make_mock_numpy())


# Mock config 包
_config_pkg = types.ModuleType("config")
_config_pkg.__path__ = []
sys.modules["config"] = _config_pkg


def _make_mock_config_db():
    m = types.ModuleType("config.database")
    m.engine = None
    m.execute_sql = lambda *a, **k: __import__("pandas").DataFrame()
    m.save_to_database = lambda *a, **k: True
    return m


sys.modules.setdefault("config.database", _make_mock_config_db())


def test_portfolio_structure():
    """测试 1：portfolio/ 包结构完整。"""
    assert (ROOT / "portfolio" / "__init__.py").exists()
    assert (ROOT / "portfolio" / "universe.py").exists()
    assert (ROOT / "portfolio" / "strategy.py").exists()
    print("[OK] portfolio/ 包结构完整（__init__.py + universe.py + strategy.py）")


def test_backtest_structure():
    """测试 2：backtest/ 包结构完整。"""
    assert (ROOT / "backtest" / "__init__.py").exists()
    assert (ROOT / "backtest" / "metrics.py").exists()
    assert (ROOT / "backtest" / "engine.py").exists()
    print("[OK] backtest/ 包结构完整（__init__.py + metrics.py + engine.py）")


def test_signals_structure():
    """测试 3：signals/ 包结构完整。"""
    assert (ROOT / "signals" / "__init__.py").exists()
    assert (ROOT / "signals" / "generator.py").exists()
    print("[OK] signals/ 包结构完整（__init__.py + generator.py）")


def test_strategy_methods():
    """测试 4：CrossSectionalMomentumStrategy 有必要方法。"""
    from portfolio.strategy import CrossSectionalMomentumStrategy
    s = CrossSectionalMomentumStrategy()
    assert hasattr(s, "compute_target_weights")
    assert hasattr(s, "apply_constraints")
    assert hasattr(s, "get_universe")
    assert hasattr(s, "compute_momentum")
    assert hasattr(s, "compute_volatility")
    # 默认参数
    assert s.lookback == 20
    assert s.max_positions == 5
    assert s.max_weight == 0.3
    assert s.max_drawdown == 0.10
    print("[OK] CrossSectionalMomentumStrategy 方法完整 + 默认参数正确")


def test_backtester_methods():
    """测试 5：VectorizedBacktester 有 run 方法。"""
    from backtest.engine import VectorizedBacktester
    from portfolio.strategy import CrossSectionalMomentumStrategy
    s = CrossSectionalMomentumStrategy()
    bt = VectorizedBacktester(strategy=s)
    assert hasattr(bt, "run")
    assert hasattr(bt, "get_trading_dates")
    assert hasattr(bt, "get_returns_data")
    assert hasattr(bt, "is_rebalance_day")
    assert bt.rebalance_freq == "W"
    assert bt.commission == 0.0005
    print("[OK] VectorizedBacktester 方法完整 + 默认参数正确")


def test_compute_metrics():
    """测试 6：compute_metrics 计算正确（纯 Python 实现，用 list 测）。"""
    from backtest.metrics import compute_metrics
    # 构造简单收益序列
    returns = [0.01, -0.02, 0.03, 0.01, -0.01]
    m = compute_metrics(returns)
    expected_keys = {
        "total_return", "annual_return", "annual_volatility",
        "sharpe", "max_drawdown", "calmar", "win_rate",
    }
    assert set(m.keys()) == expected_keys, f"指标键不匹配: {set(m.keys())}"
    # 胜率：3 正 2 负 → 0.6
    assert m["win_rate"] == 0.6, f"胜率应为 0.6，实际 {m['win_rate']}"
    # 空序列
    m_empty = compute_metrics([])
    assert m_empty["sharpe"] == 0.0
    assert m_empty["win_rate"] == 0.0
    # 单元素序列（不崩溃）
    m_single = compute_metrics([0.01])
    assert m_single["annual_volatility"] == 0.0
    print("[OK] compute_metrics 计算正确（含 7 个指标 + 空序列 + 单元素处理）")


def test_signal_generator_methods():
    """测试 7：SignalGenerator 有 generate / save 方法。"""
    from signals.generator import SignalGenerator
    from portfolio.strategy import CrossSectionalMomentumStrategy
    s = CrossSectionalMomentumStrategy()
    gen = SignalGenerator(strategy=s, strategy_name="etf_momentum")
    assert hasattr(gen, "generate")
    assert hasattr(gen, "save")
    assert hasattr(gen, "get_latest_trade_date")
    assert hasattr(gen, "get_current_holdings")
    assert gen.strategy_name == "etf_momentum"
    print("[OK] SignalGenerator 方法完整")


def test_signals_reuses_strategy():
    """测试 8：signals 复用 portfolio.strategy（不重复实现策略逻辑）。"""
    sg_content = (ROOT / "signals" / "generator.py").read_text(encoding="utf-8")
    assert "compute_target_weights" in sg_content, "SignalGenerator 应调用 strategy.compute_target_weights"
    assert "apply_constraints" in sg_content, "SignalGenerator 应调用 strategy.apply_constraints"
    # 不应自己实现动量/波动率计算
    assert "def compute_momentum" not in sg_content, "SignalGenerator 不应重复实现 compute_momentum"
    assert "def compute_volatility" not in sg_content, "SignalGenerator 不应重复实现 compute_volatility"
    print("[OK] signals 复用 portfolio.strategy（不重复实现策略逻辑）")


def test_backtest_reuses_strategy():
    """测试 9：backtest 复用 portfolio.strategy（engine 调用 strategy.compute_target_weights）。"""
    eng_content = (ROOT / "backtest" / "engine.py").read_text(encoding="utf-8")
    assert "compute_target_weights" in eng_content, "VectorizedBacktester 应调用 strategy.compute_target_weights"
    assert "apply_constraints" in eng_content, "VectorizedBacktester 应调用 strategy.apply_constraints"
    # 不应自己实现动量/波动率计算
    assert "def compute_momentum" not in eng_content, "VectorizedBacktester 不应重复实现 compute_momentum"
    print("[OK] backtest 复用 portfolio.strategy（engine 调用 strategy.compute_target_weights）")


def test_etf_pool_nonempty():
    """测试 10：DEFAULT_ETF_POOL 非空，含宽基/行业/风格三类。"""
    from portfolio.universe import DEFAULT_ETF_POOL, get_etf_universe
    assert len(DEFAULT_ETF_POOL) >= 10, f"ETF 池应至少 10 个，实际 {len(DEFAULT_ETF_POOL)}"
    categories = {info["category"] for info in DEFAULT_ETF_POOL.values()}
    assert categories == {"broad", "industry", "style"}, f"应含三类，实际 {categories}"
    # 按分类筛选
    broad = get_etf_universe("broad")
    industry = get_etf_universe("industry")
    style = get_etf_universe("style")
    all_codes = get_etf_universe("all")
    assert len(broad) > 0 and len(industry) > 0 and len(style) > 0
    assert len(all_codes) == len(broad) + len(industry) + len(style)
    print(f"[OK] DEFAULT_ETF_POOL 含 {len(DEFAULT_ETF_POOL)} 个 ETF（宽基{len(broad)}+行业{len(industry)}+风格{len(style)}）")


def test_signal_table_prefix():
    """测试 11：表名前缀 signal_（signals 层）。"""
    sg_content = (ROOT / "signals" / "generator.py").read_text(encoding="utf-8")
    assert "signal_rebalance" in sg_content, "signals 层应使用 signal_rebalance 表"
    print("[OK] signals 层表名前缀 signal_rebalance")


def test_no_legacy_imports():
    """测试 12：三层不依赖旧 data/config、data/utils。"""
    for subdir in ["portfolio", "backtest", "signals"]:
        dir_path = ROOT / subdir
        for py in dir_path.glob("*.py"):
            content = py.read_text(encoding="utf-8")
            assert "data.config" not in content, f"{subdir}/{py.name} 不应依赖 data.config"
            assert "data.utils" not in content, f"{subdir}/{py.name} 不应依赖 data.utils"
            assert "data.sql" not in content, f"{subdir}/{py.name} 不应依赖 data.sql"
    print("[OK] portfolio/backtest/signals 不依赖旧 data/config、data/utils、data/sql")


if __name__ == "__main__":
    print("=" * 60)
    print("Step 10 验收测试：portfolio/ + backtest/ + signals/")
    print("=" * 60)
    test_portfolio_structure()
    test_backtest_structure()
    test_signals_structure()
    test_strategy_methods()
    test_backtester_methods()
    test_compute_metrics()
    test_signal_generator_methods()
    test_signals_reuses_strategy()
    test_backtest_reuses_strategy()
    test_etf_pool_nonempty()
    test_signal_table_prefix()
    test_no_legacy_imports()
    print("=" * 60)
    print("所有验收测试通过 ✅")
    print("=" * 60)
