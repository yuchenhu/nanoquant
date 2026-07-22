"""回测指标计算（年化、夏普、最大回撤、胜率、Calmar）。

纯 Python 实现，不依赖 pandas/numpy，便于测试和 Python 3.14 兼容。
接受 list[float] 或 pandas.Series（取 .tolist()）。
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Union


def _to_list(returns: Union[Iterable[float], "object"]) -> List[float]:
    """统一转 list[float]。"""
    if returns is None:
        return []
    if hasattr(returns, "tolist"):
        returns = returns.tolist()
    if isinstance(returns, (list, tuple)):
        return list(returns)
    return list(returns)


def compute_metrics(returns: Iterable[float], freq: int = 252) -> Dict[str, float]:
    """计算回测指标。

    returns: 日收益率序列（list 或 pd.Series）
    freq: 年化因子（252 交易日）
    """
    data = _to_list(returns)
    if not data:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "win_rate": 0.0,
        }

    # 累计净值
    nav = 1.0
    nav_list: List[float] = []
    for r in data:
        nav *= (1 + r)
        nav_list.append(nav)
    total_return = nav_list[-1] - 1

    # 年化收益
    n_days = len(data)
    annual_return = (1 + total_return) ** (freq / n_days) - 1 if n_days > 0 else 0.0

    # 年化波动率（样本标准差）
    if n_days >= 2:
        mean = sum(data) / n_days
        var = sum((x - mean) ** 2 for x in data) / (n_days - 1)
        daily_std = math.sqrt(var)
    else:
        daily_std = 0.0
    annual_vol = daily_std * math.sqrt(freq)

    # 夏普（无风险利率=0）
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0.0

    # 最大回撤
    peak = nav_list[0]
    max_drawdown = 0.0
    for v in nav_list:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, dd)

    # Calmar
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else 0.0

    # 胜率
    win_count = sum(1 for r in data if r > 0)
    win_rate = win_count / n_days

    return {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "annual_volatility": round(annual_vol, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 4),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
    }
