"""
3ETF轮动_风控：每日按动量排名，持有动量最大的 4 只 ETF，并添加风险控制逻辑；
如果标准差_score>0.03或者变异系数_CV>0.7则自动减半仓；
每次调仓的时候，如果标准差_score>0.03或者变异系数_CV>0.7则只买入原本金额的一半；
T 日收盘后根据动量选股，T+1 日以当日开高低收均价成交；佣金万 5、单笔最低 5 元。
"""
from __future__ import annotations

import os
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

INPUT_PATH = Path("output/ETF日线宽表_因子添加.csv")
OUTPUT_DIR = Path("output")
BACKTEST_DIR = OUTPUT_DIR / "ETF轮动回测结果"
INITIAL_CAPITAL = 40000
COMMISSION_RATE = 0.0005  # 万 5
COMMISSION_MIN = 0.01

# 风险控制参数

RISK_CONTROL_STD_THRESHOLD = 0.03  # 标准差阈值
RISK_CONTROL_CV_THRESHOLD = 0.5    # 变异系数阈值


def load_env_file():
    """从同目录 .env 读取环境变量（不覆盖系统同名变量）。"""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        # 去除value中的注释部分
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env_params():
    """从环境变量获取回测参数"""
    load_env_file()
    
    # 读取ETF持有数量，从环境变量ETF_HOLD_NUM读取
    etf_hold_num = os.getenv("ETF_HOLD_NUM")
    if etf_hold_num:
        try:
            etf_hold_num = int(etf_hold_num)
        except ValueError:
            etf_hold_num = 4
    else:
        etf_hold_num = 4
    
    # 读取止损率
    stop_loss_rate = os.getenv("STOP_LOSS_RATE")
    if stop_loss_rate:
        try:
            stop_loss_rate = float(stop_loss_rate)
        except ValueError:
            stop_loss_rate = 0.1
    else:
        stop_loss_rate = 0.1
    
    # 读取调仓周期
    hold_time = os.getenv("HOLD_TIME")
    if hold_time:
        try:
            hold_time = int(hold_time)
        except ValueError:
            hold_time = 5
    else:
        hold_time = 5
    
    return etf_hold_num, stop_loss_rate, hold_time


def commission(notional: float) -> float:
    if notional <= 0:
        return 0.0
    return max(notional * COMMISSION_RATE, COMMISSION_MIN)


def cash_after_buy(cash: float, price: float) -> tuple[float, float, float]:
    """用全部现金按价买入，返回 (股数, 佣金, 剩余现金)。"""
    if cash <= 0 or price <= 0 or not np.isfinite(price):
        return 0.0, 0.0, cash

    def total_cost_for_shares(sh: float) -> float:
        n = sh * price
        return n + commission(n)

    lo, hi = 0.0, cash / price
    for _ in range(64):
        mid = (lo + hi) / 2.0
        if total_cost_for_shares(mid) <= cash:
            lo = mid
        else:
            hi = mid
    shares = lo
    notional = shares * price
    fee = commission(notional)
    leftover = cash - notional - fee
    return shares, fee, leftover


def proceeds_after_sell(shares: float, price: float) -> float:
    if shares <= 0 or price <= 0 or not np.isfinite(price):
        return 0.0
    notional = shares * price
    return notional - commission(notional)


def get_etf_metrics(d: pd.Timestamp, df: pd.DataFrame) -> pd.DataFrame:
    """获取指定日期所有ETF的动量和风险参数"""
    sub = df[(df["trade_date"] == d) & df["动量"].notna()]
    if sub.empty:
        return pd.DataFrame()
    return sub[['ts_code', '动量', '标准差_score', '变异系数_CV']].sort_values('动量', ascending=False)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    
    # 获取环境变量参数
    etf_hold_num, stop_loss_rate, hold_time = get_env_params()
    print(f"回测参数：")
    print(f"  ETF持有数量: {etf_hold_num}")
    print(f"  止损率: {stop_loss_rate}")
    print(f"  调仓周期: {hold_time} 个交易日")
    print(f"  风险控制阈值 - 标准差: {RISK_CONTROL_STD_THRESHOLD}")
    print(f"  风险控制阈值 - 变异系数: {RISK_CONTROL_CV_THRESHOLD}")

    df = pd.read_csv(INPUT_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ("open", "high", "low", "close", "动量", "标准差_score", "变异系数_CV"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ohlc4"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    df = df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    dates = sorted(df["trade_date"].unique())
    if len(dates) < 2:
        raise ValueError("交易日少于 2 天，无法 T+1 执行。")

    date_to_i = {d: i for i, d in enumerate(dates)}
    ohlc4_raw = df.pivot(index="trade_date", columns="ts_code", values="ohlc4") # 调仓价
    close_raw = df.pivot(index="trade_date", columns="ts_code", values="close") # 计算权益估值
    # 统一交易日历；缺失日向前填充，仅用于成交与估值，避免调仓日无 bar 时卖出价为 NaN
    price_close = close_raw.reindex(dates).ffill()
    price_ohlc4 = ohlc4_raw.reindex(dates).ffill()
    
    # 获取因子数据
    std_raw = df.pivot(index="trade_date", columns="ts_code", values="标准差_score")
    cv_raw = df.pivot(index="trade_date", columns="ts_code", values="变异系数_CV")
    price_std = std_raw.reindex(dates).ffill()
    price_cv = cv_raw.reindex(dates).ffill()

    def winners_on(d: pd.Timestamp) -> list[str]:
        idx = date_to_i.get(d)
        if idx is None or idx + 1 >= len(dates):
            return []
        nxt = dates[idx + 1]
        sub = df[(df["trade_date"] == d) & df["动量"].notna()]
        if sub.empty:
            return []
        codes_ok = []
        for c in sub["ts_code"].unique():
            if c not in ohlc4_raw.columns or c not in close_raw.columns:
                continue
            if nxt not in ohlc4_raw.index:
                continue
            px = ohlc4_raw.loc[nxt, c]
            if np.isfinite(float(px)):
                codes_ok.append(c)
        if not codes_ok:
            return []
        sub = sub[sub["ts_code"].isin(codes_ok)]
        if sub.empty:
            return []
        # 按动量降序排列，选择前N个
        top_n = sub.nlargest(etf_hold_num, "动量")
        return list(top_n["ts_code"])

    # 生成调仓信号
    winners_list: list[list[str]] = [winners_on(d) for d in dates]
    
    # 确定调仓日期
    rebalance_dates = []
    for i in range(len(dates) - 1):
        # 每HOLD_TIME个交易日调仓一次
        if i % hold_time == 0:
            rebalance_dates.append(dates[i + 1])

    # 构建调仓事件
    events: dict[pd.Timestamp, tuple[list[str], list[str]]] = {}
    prev_winners: list[str] = []
    
    for i in range(len(dates) - 1):
        current_date = dates[i]
        exec_date = dates[i + 1]
        
        # 检查是否是调仓日
        if exec_date not in rebalance_dates:
            continue
        
        current_winners = winners_list[i]
        if current_winners != prev_winners:
            events[exec_date] = (prev_winners, current_winners)
            prev_winners = current_winners

    # 初始化回测状态
    cash = INITIAL_CAPITAL
    holdings = {}
    buy_prices = {}
    buy_dates = {}
    # 跟踪每个ETF是否已经触发过风险减半
    risk_half_triggered = {}  # code -> True/False

    records: list[dict] = []
    nav_rows: list[dict] = []

    for t in dates:
        # 检查风险控制条件，逐日检测，每个标的只触发一次
        risk_half_position = []
        for code, shares in holdings.items():
            # 检查是否已经触发过风险减半
            if risk_half_triggered.get(code, False):
                continue
            if code in price_std.columns and code in price_cv.columns:
                std_value = float(price_std.loc[t, code])
                cv_value = float(price_cv.loc[t, code])
                
                if np.isfinite(std_value) and np.isfinite(cv_value):
                    if std_value > RISK_CONTROL_STD_THRESHOLD or cv_value > RISK_CONTROL_CV_THRESHOLD:
                        risk_half_position.append(code)
        
        # 执行风险减半仓
        if risk_half_position:
            for code in risk_half_position:
                if code in holdings and holdings[code] > 0:
                    # 卖出一半
                    sell_shares = holdings[code] / 2
                    sell_px = float(price_ohlc4.loc[t, code])
                    if np.isfinite(sell_px) and sell_px > 0:
                        cash += proceeds_after_sell(sell_shares, sell_px)
                        holdings[code] -= sell_shares
                        # 标记该ETF已触发过风险减半
                        risk_half_triggered[code] = True
                        
                        records.append({
                            "exec_date": t.strftime("%Y-%m-%d"),
                            "signal_date": t.strftime("%Y-%m-%d"),
                            "sell_code": code,
                            "buy_code": "",
                            "sell_price_ohlc4": sell_px,
                            "buy_price_ohlc4": np.nan,
                            "sell_commission": commission(sell_shares * sell_px),
                            "buy_commission": 0.0,
                            "cash_before_trade": cash - proceeds_after_sell(sell_shares, sell_px),
                            "nav_after_trade_eod": cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items()),
                            "action": "风险减半仓",
                            "holdings": ",".join(holdings.keys()),
                            "cash_ratio": cash / (cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())),
                            "holding_ratios": ",".join([f"{c}:{shares * float(price_close.loc[t, c]) / (cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())):.4f}" for c, shares in holdings.items()])
                        })
        
        # 检查止损条件
        sell_for_stop_loss = []
        for code, shares in holdings.items():
            if code in price_close.columns:
                current_price = float(price_close.loc[t, code])
                if np.isfinite(current_price) and code in buy_prices:
                    buy_price = buy_prices[code]
                    if (current_price - buy_price) / buy_price <= -stop_loss_rate:
                        sell_for_stop_loss.append(code)
        
        # 执行止损卖出
        if sell_for_stop_loss:
            for code in sell_for_stop_loss:
                if code in holdings and holdings[code] > 0:
                    sell_px = float(price_ohlc4.loc[t, code])
                    if np.isfinite(sell_px) and sell_px > 0:
                        cash += proceeds_after_sell(holdings[code], sell_px)
                        del holdings[code]
                        if code in buy_prices:
                            del buy_prices[code]
                        # 清除该ETF的风险减半触发标记
                        if code in risk_half_triggered:
                            del risk_half_triggered[code]
                        
                        records.append({
                            "exec_date": t.strftime("%Y-%m-%d"),
                            "signal_date": t.strftime("%Y-%m-%d"),
                            "sell_code": code,
                            "buy_code": "",
                            "sell_price_ohlc4": sell_px,
                            "buy_price_ohlc4": np.nan,
                            "sell_commission": commission(holdings.get(code, 0) * sell_px),
                            "buy_commission": 0.0,
                            "cash_before_trade": cash - proceeds_after_sell(holdings.get(code, 0), sell_px),
                            "nav_after_trade_eod": cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items()),
                            "action": "止损",
                            "holdings": ",".join(holdings.keys()),
                            "cash_ratio": cash / (cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())),
                            "holding_ratios": ",".join([f"{c}:{shares * float(price_close.loc[t, c]) / (cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())):.4f}" for c, shares in holdings.items()])
                        })
        
        # 执行调仓
        if t in events:
            old_winners, new_winners = events[t]
            
            # 卖出不在新名单中的ETF
            to_sell = [code for code in old_winners if code not in new_winners and code in holdings]
            sell_fees = 0.0
            
            for code in to_sell:
                if code in holdings and holdings[code] > 0:
                    sell_px = float(price_ohlc4.loc[t, code])
                    if np.isfinite(sell_px) and sell_px > 0:
                        cash += proceeds_after_sell(holdings[code], sell_px)
                        sell_fees += commission(holdings[code] * sell_px)
                        del holdings[code]
                        if code in buy_prices:
                            del buy_prices[code]
                        # 清除该ETF的风险减半触发标记
                        if code in risk_half_triggered:
                            del risk_half_triggered[code]
            
            # 买入新名单中的ETF
            if new_winners:
                # 计算需要买入的ETF数量（排除已经在持仓中的）
                codes_to_buy = [code for code in new_winners if code not in holdings]
                
                if codes_to_buy:
                    # 用全部现金均分买入
                    per_etf_cash = cash / len(codes_to_buy)
                    buy_fees = 0.0
                    
                    for code in codes_to_buy:
                        # 检查风险控制条件
                        risk_adjustment = 1.0
                        if code in price_std.columns and code in price_cv.columns:
                            std_value = float(price_std.loc[t, code])
                            cv_value = float(price_cv.loc[t, code])
                            if np.isfinite(std_value) and np.isfinite(cv_value):
                                if std_value > RISK_CONTROL_STD_THRESHOLD or cv_value > RISK_CONTROL_CV_THRESHOLD:
                                    risk_adjustment = 0.5  # 只买入原本金额的一半
                        
                        buy_px = float(price_ohlc4.loc[t, code])
                        if np.isfinite(buy_px) and buy_px > 0:
                            # 根据风险调整买入金额
                            adjusted_cash = per_etf_cash * risk_adjustment
                            shares, fee, cash_left = cash_after_buy(adjusted_cash, buy_px)
                            if shares > 0:
                                holdings[code] = shares
                                buy_prices[code] = buy_px
                                buy_dates[code] = t
                                buy_fees += fee
                                cash -= (adjusted_cash - cash_left)  # 更新剩余现金
            
            # 记录调仓事件
            for code in to_sell:
                total_nav = cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())
                records.append({
                    "exec_date": t.strftime("%Y-%m-%d"),
                    "signal_date": dates[date_to_i[t] - 1].strftime("%Y-%m-%d"),
                    "sell_code": code,
                    "buy_code": "",
                    "sell_price_ohlc4": float(price_ohlc4.loc[t, code]) if code in price_ohlc4.columns else np.nan,
                    "buy_price_ohlc4": np.nan,
                    "sell_commission": commission(holdings.get(code, 0) * float(price_ohlc4.loc[t, code]) if code in price_ohlc4.columns else 0),
                    "buy_commission": 0.0,
                    "cash_before_trade": cash + sum(proceeds_after_sell(holdings.get(c, 0), float(price_ohlc4.loc[t, c]) if c in price_ohlc4.columns else 0) for c in to_sell),
                    "nav_after_trade_eod": total_nav,
                    "action": "调仓卖出",
                    "holdings": ",".join(holdings.keys()),
                    "cash_ratio": cash / total_nav,
                    "holding_ratios": ",".join([f"{c}:{shares * float(price_close.loc[t, c]) / total_nav:.4f}" for c, shares in holdings.items()])
                })
            
            for code in new_winners:
                if code not in old_winners:
                    total_nav = cash + sum(shares * float(price_close.loc[t, c]) if c in price_close.columns else 0 for c, shares in holdings.items())
                    records.append({
                        "exec_date": t.strftime("%Y-%m-%d"),
                        "signal_date": dates[date_to_i[t] - 1].strftime("%Y-%m-%d"),
                        "sell_code": "",
                        "buy_code": code,
                        "sell_price_ohlc4": np.nan,
                        "buy_price_ohlc4": float(price_ohlc4.loc[t, code]) if code in price_ohlc4.columns else np.nan,
                        "sell_commission": 0.0,
                        "buy_commission": commission(holdings.get(code, 0) * float(price_ohlc4.loc[t, code]) if code in price_ohlc4.columns else 0),
                        "cash_before_trade": cash + sum(holdings.get(c, 0) * float(price_ohlc4.loc[t, c]) if c in price_ohlc4.columns else 0 for c in new_winners),
                        "nav_after_trade_eod": total_nav,
                        "action": "调仓买入",
                        "holdings": ",".join(holdings.keys()),
                        "cash_ratio": cash / total_nav,
                        "holding_ratios": ",".join([f"{c}:{shares * float(price_close.loc[t, c]) / total_nav:.4f}" for c, shares in holdings.items()])
                    })
        
        # 计算现金利息（年化1%）
        daily_interest_rate = 0.01 / 365
        cash_interest = cash * daily_interest_rate
        cash += cash_interest
        
        # 计算当日净值
        nav = cash
        for code, shares in holdings.items():
            if code in price_close.columns:
                c = price_close.loc[t, code]
                if np.isfinite(c):
                    nav += shares * float(c)
        
        # 计算持仓比例
        cash_ratio = cash / nav if nav > 0 else 0
        holding_ratios = []
        for code, shares in holdings.items():
            if code in price_close.columns:
                c = price_close.loc[t, code]
                if np.isfinite(c):
                    holding_value = shares * float(c)
                    holding_ratio = holding_value / nav if nav > 0 else 0
                    holding_ratios.append(f"{code}:{holding_ratio:.4f}")
        
        nav_rows.append({"trade_date": t.strftime("%Y-%m-%d"), "nav": nav, "holdings": ",".join(holdings.keys()), "cash_ratio": cash_ratio, "holding_ratios": ",".join(holding_ratios)})

    nav_df = pd.DataFrame(nav_rows)
    nav_df["nav"] = pd.to_numeric(nav_df["nav"], errors="coerce")
    nav_df["return"] = nav_df["nav"].pct_change()

    start_nav = float(nav_df["nav"].iloc[0])
    end_nav = float(nav_df["nav"].iloc[-1])
    total_ret = end_nav / start_nav - 1.0
    n = len(nav_df) - 1
    years = n / 252.0 if n > 0 else 0.0
    ann_ret = (end_nav / start_nav) ** (1 / years) - 1.0 if years > 0 and start_nav > 0 else np.nan

    daily_ret = nav_df["return"].dropna()
    vol_ann = float(daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 else np.nan
    sharpe = (
        float(np.sqrt(252) * daily_ret.mean() / daily_ret.std())
        if len(daily_ret) > 1 and daily_ret.std() > 0
        else np.nan
    )

    cummax = nav_df["nav"].cummax()
    drawdown = nav_df["nav"] / cummax - 1.0
    max_dd = float(drawdown.min())

    metrics = pd.DataFrame(
        [
            {"metric": "初始资金", "value": INITIAL_CAPITAL},
            {"metric": "期末净值", "value": end_nav},
            {"metric": "总收益率", "value": total_ret},
            {"metric": "年化收益率", "value": ann_ret},
            {"metric": "年化波动率", "value": vol_ann},
            {"metric": "夏普比率(无风险利率=0,252日)", "value": sharpe},
            {"metric": "最大回撤", "value": max_dd},
            {"metric": "调仓次数", "value": len([r for r in records if r.get("action", "") in ["调仓买入", "调仓卖出"]]) // 2},
            {"metric": "止损次数", "value": len([r for r in records if r.get("action", "") == "止损"])},
            {"metric": "风险减半仓次数", "value": len([r for r in records if r.get("action", "") == "风险减半仓"])},
            {"metric": "回测区间(交易日)", "value": len(nav_df)},
            {"metric": "ETF持有数量", "value": etf_hold_num},
            {"metric": "止损率", "value": stop_loss_rate},
            {"metric": "调仓周期(交易日)", "value": hold_time},
            {"metric": "标准差风险阈值", "value": RISK_CONTROL_STD_THRESHOLD},
            {"metric": "变异系数风险阈值", "value": RISK_CONTROL_CV_THRESHOLD},
        ]
    )

    rebalance_df = pd.DataFrame(records)
    metrics_path = BACKTEST_DIR / "backtest_metrics.csv"
    nav_path = BACKTEST_DIR / "backtest_daily_nav.csv"
    rebalance_path = BACKTEST_DIR / "rebalance_records.csv"
    curve_path = BACKTEST_DIR / "backtest_equity_curve.png"

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    net_value = nav_df["nav"] / INITIAL_CAPITAL
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(pd.to_datetime(nav_df["trade_date"]), net_value, color="#1f77b4", linewidth=1.2)
    ax.set_title(f"ETF轮动回测 — 组合净值曲线（期初=1）")
    ax.set_xlabel("日期")
    ax.set_ylabel("净值")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    
    # 打印最后一个交易日的ETF动量和风险参数
    last_date = dates[-1]
    print(f"\n=== 最后交易日({last_date.strftime('%Y-%m-%d')})ETF动量和风险参数 ===")
    etf_metrics = get_etf_metrics(last_date, df)
    if not etf_metrics.empty:
        print(etf_metrics.to_string(index=False))
    else:
        print("无数据")

    # 打印当前持仓ETF的浮盈和买入价
    print(f"\n=== 当前持仓ETF浮盈分析 ===")
    if holdings:
        for code, shares in holdings.items():
            if code in price_close.columns and code in buy_prices:
                current_price = float(price_close.loc[last_date, code])
                buy_price = buy_prices[code]
                buy_date = buy_dates.get(code, None)
                if np.isfinite(current_price) and np.isfinite(buy_price) and buy_price > 0:
                    profit = (current_price - buy_price) * shares
                    profit_ratio = (current_price - buy_price) / buy_price
                    buy_date_str = buy_date.strftime('%Y-%m-%d') if buy_date else '未知'
                    print(f"  {code}: 买入日期={buy_date_str}, 买入价={buy_price:.4f}, 当前价={current_price:.4f}, 浮盈={profit:.2f}, 浮盈率={profit_ratio*100:.2f}%")
    else:
        print("  当前无持仓")

    # 计算当天净值、持仓比例、现金比例
    total_nav = cash
    holding_details = []
    for code, shares in holdings.items():
        if code in price_close.columns:
            current_price = float(price_close.loc[last_date, code])
            if np.isfinite(current_price):
                holding_value = shares * current_price
                total_nav += holding_value
                holding_details.append((code, shares, current_price, holding_value))
    
    cash_ratio = cash / total_nav if total_nav > 0 else 0
    print(f"\n=== 账户概况 ===")
    print(f"  总资产: {total_nav:.2f}")
    print(f"  现金: {cash:.2f} (现金比例: {cash_ratio*100:.2f}%)")
    print(f"  持仓市值: {total_nav - cash:.2f} (持仓比例: {(1-cash_ratio)*100:.2f}%)")
    
    # 打印持仓详情
    print(f"\n=== 持仓详情 ===")
    for code, shares, current_price, holding_value in holding_details:
        holding_ratio = holding_value / total_nav if total_nav > 0 else 0
        print(f"  {code}: 数量={shares:.2f}, 当前价={current_price:.4f}, 持仓市值={holding_value:.2f}, 持仓比例={holding_ratio*100:.2f}%")
    
    # 获取当天收益率
    last_nav_row = nav_df[nav_df['trade_date'] == last_date.strftime('%Y-%m-%d')]
    if not last_nav_row.empty:
        last_nav = float(last_nav_row['nav'].iloc[0])
        if len(nav_df) > 1:
            prev_date = dates[date_to_i[last_date] - 1].strftime('%Y-%m-%d')
            prev_nav_row = nav_df[nav_df['trade_date'] == prev_date]
            if not prev_nav_row.empty:
                prev_nav = float(prev_nav_row['nav'].iloc[0])
                daily_return = (last_nav - prev_nav) / prev_nav * 100 if prev_nav > 0 else 0
                print(f"\n=== 当日收益 ===")
                print(f"  当日收益率: {daily_return:.2f}%")
    
    # 检查当天是否进行了止损
    stop_loss_records = [r for r in records if r.get('action') == '止损' and r.get('exec_date') == last_date.strftime('%Y-%m-%d')]
    if stop_loss_records:
        print(f"\n  当日止损: 是")
        for r in stop_loss_records:
            print(f"    止损卖出: {r.get('sell_code', '')}, 价格: {r.get('sell_price_ohlc4', 0):.4f}")
    else:
        print(f"\n  当日止损: 否")

    # 分析下一日操作建议（根据当前日计算下一调仓日）
    print(f"\n=== 下一日操作建议 ===")
    # 找到下一个调仓日
    last_date_idx = date_to_i[last_date]
    next_rebalance_date = None
    for i in range(last_date_idx + 1, len(dates)):
        if dates[i] in rebalance_dates:
            next_rebalance_date = dates[i]
            break
    
    # 找到当前所属的调仓周期开始日（动量排序日）
    # rebalance_dates中的日期是执行调仓的日期（T+1日），动量排序是在前一天（T日）进行的
    # 需要根据索引找到排序日，因为dates可能跳过非交易日
    current_rebalance_exec_date = None
    current_rebalance_exec_idx = None
    for rd in sorted(rebalance_dates):
        if rd <= last_date:
            current_rebalance_exec_date = rd
            current_rebalance_exec_idx = date_to_i[rd]
        else:
            break
    
    if current_rebalance_exec_date and current_rebalance_exec_idx > 0:
        # 排序日是执行日的前一个交易日（即dates中的前一个元素）
        current_rebalance_cycle_start = dates[current_rebalance_exec_idx - 1]
        # 计算交易日数量（从排序日到当前日）
        trading_days_since_sort = last_date_idx - (current_rebalance_exec_idx - 1)
        countdown = hold_time - trading_days_since_sort
        if countdown < 0:
            countdown = 0
        print(f"调仓倒计时: {countdown} 个交易日 (动量排序日: {current_rebalance_cycle_start.strftime('%Y-%m-%d')}, 执行日: {current_rebalance_exec_date.strftime('%Y-%m-%d')})")
    else:
        countdown = hold_time
        print(f"调仓倒计时: {countdown} 个交易日 (首次建仓)")
    
    if next_rebalance_date:
        print(f"下一个调仓日: {next_rebalance_date.strftime('%Y-%m-%d')}")
    
    # 根据当前日的前一个交易日计算信号（因为winners_on需要T+1可执行）
    # winners_on(d)返回的是T+1日应该持有的ETF
    # 所以winners_on(dates[last_date_idx - 1])返回最后一天应该持有的ETF
    signal_date_idx = last_date_idx - 1 if last_date_idx > 0 else None
    current_winners = winners_on(dates[signal_date_idx]) if signal_date_idx is not None else []
    current_holding_codes = list(holdings.keys())
    
    # 检查是否有持仓ETF触发风险降仓
    risk_codes = []
    for code in current_holding_codes:
        if code in price_std.columns and code in price_cv.columns:
            std_value = float(price_std.loc[last_date, code])
            cv_value = float(price_cv.loc[last_date, code])
            if np.isfinite(std_value) and np.isfinite(cv_value):
                if std_value > RISK_CONTROL_STD_THRESHOLD or cv_value > RISK_CONTROL_CV_THRESHOLD:
                    risk_codes.append((code, std_value, cv_value))
    
    # 计算需要买入和卖出的ETF
    to_buy = [code for code in current_winners if code not in current_holding_codes]
    to_sell = [code for code in current_holding_codes if code not in current_winners]
    to_hold = [code for code in current_winners if code in current_holding_codes]
    
    print(f"当前持仓: {current_holding_codes}")
    print(f"建议持有: {current_winners}")
    
    print(f"\n操作建议:")
    # 判断是否为调仓日
    is_rebalance_day = last_date in rebalance_dates
    
    if is_rebalance_day:
        # 调仓日执行调仓
        if to_hold:
            print(f"  持有: {to_hold}")
        if to_buy:
            print(f"  买入: {to_buy}")
            for code in to_buy:
                if code in price_std.columns and code in price_cv.columns:
                    std_value = float(price_std.loc[last_date, code])
                    cv_value = float(price_cv.loc[last_date, code])
                    risk_status = "高风险" if std_value > RISK_CONTROL_STD_THRESHOLD or cv_value > RISK_CONTROL_CV_THRESHOLD else "正常"
                    risk_note = "（建议半仓买入）" if risk_status == "高风险" else ""
                    print(f"    {code}: 标准差={std_value:.4f}, 变异系数={cv_value:.4f}, {risk_status}{risk_note}")
        if to_sell:
            print(f"  卖出: {to_sell}")
        if not to_buy and not to_sell:
            print("  无需调仓")
    else:
        # 非调仓日，仅风险降仓
        if risk_codes:
            print(f"  风险降仓: ")
            for code, std_value, cv_value in risk_codes:
                print(f"    {code}: 标准差={std_value:.4f}, 变异系数={cv_value:.4f}, 高风险需减半仓")
        else:
            print(f"  暂不调仓（非调仓日，无风险降仓）")

    # 尝试保存文件，添加错误处理
    try:
        metrics.to_csv(metrics_path, index=False, encoding="utf-8-sig")
        print(f"已保存: {metrics_path}")
    except Exception as e:
        print(f"保存 {metrics_path} 失败: {e}")

    try:
        nav_df.to_csv(nav_path, index=False, encoding="utf-8-sig")
        print(f"已保存: {nav_path}")
    except Exception as e:
        print(f"保存 {nav_path} 失败: {e}")

    try:
        rebalance_df.to_csv(rebalance_path, index=False, encoding="utf-8-sig")
        print(f"已保存: {rebalance_path}")
    except Exception as e:
        print(f"保存 {rebalance_path} 失败: {e}")

    try:
        fig.savefig(curve_path, dpi=150)
        print(f"已保存: {curve_path}")
    except Exception as e:
        print(f"保存 {curve_path} 失败: {e}")
    finally:
        plt.close(fig)


if __name__ == "__main__":
    import sys
    import traceback
    original_stdout = sys.stdout
    sys.stdout = open('backtest_output.txt', 'w', encoding='utf-8')
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
        print(traceback.format_exc())
    finally:
        sys.stdout.close()
    sys.stdout = original_stdout
    with open('backtest_output.txt', 'r', encoding='utf-8') as f:
        content = f.read()
        print(content)
