import pandas as pd
import numpy as np
import math
from matplotlib import pyplot as plt

LOOKBACK_DAYS = 20             # 动量指标计算的回溯日期数量
INPUT_PATH = "output/ETF日线宽表.csv"
OUTPUT_PATH = "output/ETF日线宽表_因子添加.csv"


def calc_momentum_score(close_series):
    """复现五福闹春核心动量：年化收益 * R²。"""
    values = close_series.to_numpy(dtype=float)
    need_len = LOOKBACK_DAYS + 1
    scores = np.full(len(values), np.nan, dtype=float)

    for i in range(need_len - 1, len(values)):
        window = values[i - need_len + 1:i + 1]
        if np.any(window <= 0) or np.any(np.isnan(window)):
            continue

        y = np.log(window)
        x = np.arange(len(y), dtype=float)
        weights = np.linspace(1, 2, len(y))

        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_returns = math.exp(slope * 250) - 1
        y_hat = slope * x + intercept
        ss_res = np.sum(weights * (y - y_hat) ** 2)
        ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot else 0

        scores[i] = annualized_returns * r_squared

    return pd.Series(scores, index=close_series.index)


def calc_std_score(close_series):
    """计算（20日收益率的标准差+5日收益率的标准差）/2。"""
    # 计算日收益率
    returns = close_series.pct_change()
    # 计算20日标准差
    std_20 = returns.rolling(window=20).std()
    # 计算5日标准差
    std_5 = returns.rolling(window=5).std()
    # 计算平均值
    std_score = (std_20 + std_5) / 2
    return std_score


def calc_cv_score(amount_series):
    """计算变异系数 CV：20日成交额标准差/20日成交额均值。"""
    # 计算20日成交额均值
    mean_20 = amount_series.rolling(window=20).mean()
    # 计算20日成交额标准差
    std_20 = amount_series.rolling(window=20).std()
    # 计算变异系数
    cv_score = std_20 / mean_20
    return cv_score


df = pd.read_csv(INPUT_PATH)
df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

df["动量"] = df.groupby("ts_code", group_keys=False)["close"].apply(calc_momentum_score)
df["标准差_score"] = df.groupby("ts_code", group_keys=False)["close"].apply(calc_std_score)
df["变异系数_CV"] = df.groupby("ts_code", group_keys=False)["amount"].apply(calc_cv_score)
df = df.dropna()
df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
print(f"已保存: {OUTPUT_PATH}")
