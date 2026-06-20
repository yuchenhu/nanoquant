"""策略层运行脚本（Step 10）：ETF 截面轮动 MVP 闭环。

功能：
1. 回测：用 portfolio.CrossSectionalMomentumStrategy + backtest.VectorizedBacktester 跑历史回测
2. 信号：用 signals.SignalGenerator 生成最新调仓信号并落库

用法：
    # 回测（默认近 1 年）
    python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

    # 生成最新调仓信号
    python scripts/run_strategy.py --mode signal

    # 指定策略参数
    python scripts/run_strategy.py --mode signal --lookback 20 --max-positions 5
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
    from backtest.engine import VectorizedBacktester

    strategy = build_strategy(args)
    bt = VectorizedBacktester(strategy=strategy, rebalance_freq=args.freq)
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
    for k, v in metrics.items():
        if k in ("total_return", "annual_return", "annual_volatility", "max_drawdown"):
            print(f"  {k}: {v:.2%}")
        else:
            print(f"  {k}: {v}")
    print("=" * 50)


def run_signal(args) -> None:
    """生成调仓信号。"""
    from signals.generator import SignalGenerator

    strategy = build_strategy(args)
    gen = SignalGenerator(strategy=strategy, strategy_name=args.strategy_name)
    signals = gen.generate(signal_date=args.signal_date)

    if signals.empty:
        logger.warning("无信号生成")
        return

    print("\n" + "=" * 50)
    print("调仓信号")
    print("=" * 50)
    print(signals.to_string(index=False))
    print("=" * 50)

    if not args.dry_run:
        ok = gen.save(signals)
        if ok:
            logger.info(f"信号已落库 signal_rebalance（{len(signals)} 条）")
        else:
            logger.error("信号落库失败")


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

    args = parser.parse_args()

    if args.mode == "backtest":
        run_backtest(args)
    else:
        run_signal(args)


if __name__ == "__main__":
    main()
