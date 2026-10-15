"""策略层运行脚本：ETF 截面轮动回测。

真实成本（华泰涨乐财富通 ETF）：
  - 佣金: 万2.5 双向，最低5元/笔
  - 滑点: 0.1% 单边（保守）
  - 往返合计: ~0.25% per unit turnover
  - 可通过 --commission / --slippage 调整

用法：
    # 回测（默认参数）
    python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

    # 指定策略参数 + 成本
    python scripts/run_strategy.py --mode backtest --lookback 20 --max-positions 5

    # 自定义成本
    python scripts/run_strategy.py --mode backtest --commission 0.00015 --slippage 0.0005
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_strategy")


def build_strategy(args) -> "CrossSectionalMomentumStrategy":
    """构建策略实例。"""
    from portfolio.strategy import CrossSectionalMomentumStrategy

    return CrossSectionalMomentumStrategy(
        lookback=args.lookback,
        vol_window=args.vol_window,
        vol_threshold=args.vol_threshold,
        max_positions=args.max_positions,
        max_weight=args.max_weight,
        max_drawdown=args.max_drawdown,
        universe_category=args.category,
    )


def run_backtest(args) -> None:
    """运行回测。"""
    from backtest.engine import CostConfig, VectorizedBacktester

    strategy = build_strategy(args)
    cost_config = CostConfig(
        commission_rate=args.commission,
        slippage=args.slippage,
    )
    bt = VectorizedBacktester(
        strategy=strategy,
        rebalance_freq=args.freq,
        cost_config=cost_config,
        capital=args.capital,
    )
    result = bt.run(start_date=args.start, end_date=args.end)

    metrics = result.get("metrics", {})
    if not metrics:
        logger.error("回测无结果")
        return

    print("\n" + "=" * 50)
    print("回测结果")
    print("=" * 50)
    print(f"区间: {args.start} ~ {args.end}")
    print(f"调仓频率: {args.freq}")
    print(f"初始资金: {args.capital:,.0f}")
    print(f"成本模型: 佣金万{args.commission*10000:.1f} + 滑点{args.slippage*100:.1f}%")
    for k, v in metrics.items():
        if k in ("total_return", "annual_return", "annual_volatility", "max_drawdown"):
            print(f"  {k}: {v:.2%}")
        else:
            print(f"  {k}: {v}")
    print("=" * 50)


def run_signal(args) -> None:
    """生成调仓信号（已废弃）。

    signal_rebalance 接口已删除（write_mode=upsert 违反项目规则"已废弃 upsert"）。
    signals/generator.py 已删除。后续如需调仓信号，应在 portfolio/ 层用 Calculator
    模式重新实现（overwrite + partition_col=signal_date）。
    """
    logger.error(
        "signal_rebalance 接口已删除（signals/generator.py 已废弃），"
        "run_signal 暂不可用。请使用 --mode backtest 跑回测。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF 截面轮动 MVP 策略运行")
    parser.add_argument(
        "--mode", choices=["backtest", "signal"], default="signal",
        help="运行模式：backtest=回测, signal=生成调仓信号",
    )
    parser.add_argument("--start", default="20240101", help="回测开始日期 yyyymmdd")
    parser.add_argument("--end", default="20241231", help="回测结束日期 yyyymmdd")
    parser.add_argument("--freq", default="W", choices=["D", "W", "M"], help="调仓频率")
    parser.add_argument("--signal-date", default=None, help="信号日（默认最新交易日）")
    parser.add_argument("--strategy-name", default="etf_momentum", help="策略名")
    parser.add_argument("--dry-run", action="store_true", help="只生成信号不落库")
    # 策略参数
    parser.add_argument("--lookback", type=int, default=20, help="动量回看窗口")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率窗口")
    parser.add_argument("--vol-threshold", type=float, default=0.4, help="波动率过滤阈值")
    parser.add_argument("--max-positions", type=int, default=5, help="持仓数量上限")
    parser.add_argument("--max-weight", type=float, default=0.3, help="单标的仓位上限")
    parser.add_argument("--max-drawdown", type=float, default=0.10, help="回撤止损线")
    parser.add_argument(
        "--category", default="all",
        choices=["all", "broad", "industry", "style"],
        help="ETF 池分类",
    )
    # 成本参数
    parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金（元）")
    parser.add_argument("--commission", type=float, default=0.00025, help="佣金率（默认万2.5）")
    parser.add_argument("--slippage", type=float, default=0.001, help="滑点率（默认0.1%%）")

    args = parser.parse_args()

    if args.mode == "backtest":
        run_backtest(args)
    else:
        run_signal(args)


if __name__ == "__main__":
    main()
