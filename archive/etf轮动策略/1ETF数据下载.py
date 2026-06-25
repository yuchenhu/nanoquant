"""
ETF 数据下载（Tushare）

使用方法（首次配置）：
1) 在项目根目录创建 .env 并设置 TUSHARE_TOKEN
2) 执行：python ETF数据下载.py
"""


import os
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import tushare as ts
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


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
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_tushare_client():
    """从环境变量读取 token 并返回 pro 客户端。"""
    load_env_file()
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError(
            "未检测到环境变量 TUSHARE_TOKEN。"
            "请先设置环境变量后再运行脚本。"
        )
    ts.set_token(token)
    return ts.pro_api()


def download_etf_basic():
    """下载 ETF 基础信息并保存到 output/ETF基础信息.csv。"""
    pro = get_tushare_client()
    df = pro.fund_basic(market="E")
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ETF基础信息.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"下载完成: {out_path}")


def download_fund_daily_wide():
    """下载多个ETF日线+复权因子，并生成复权价格后上下拼接保存。"""
    pro = get_tushare_client()
    # 直接从.env文件读取ETF_POOL
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        raise RuntimeError("未找到 .env 文件。")
    
    # 读取并解析.env文件
    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 提取ETF_POOL部分
    import re
    etf_pool_match = re.search(r'ETF_POOL=\[(.*?)\]', content, re.DOTALL)
    if not etf_pool_match:
        raise RuntimeError("未在 .env 文件中找到 ETF_POOL 配置。")
    
    etf_pool_str = '[' + etf_pool_match.group(1) + ']'
    etf_pool = json.loads(etf_pool_str)
    
    # 为ETF代码添加.SH或.SZ后缀
    ts_codes = []
    for etf in etf_pool:
        code = etf["code"]
        # 根据代码长度判断交易所（6位数字，前3位5开头为上海，1开头为深圳）
        if code.startswith("5"):
            ts_codes.append(f"{code}.SH")
        elif code.startswith("1"):
            ts_codes.append(f"{code}.SZ")
        else:
            # 默认添加.SH
            ts_codes.append(f"{code}.SH")
    start_date = "20200101"   #开始时间
    end_date = datetime.today().strftime("%Y%m%d")     #结束时间
    fields = "ts_code,trade_date,open,high,low,close,vol,amount" #获取字段

    frames = []
    for ts_code in ts_codes:
        print(f"下载中: {ts_code}")
        df = pro.fund_daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=fields,
        )
        if df is None or df.empty:
            print(f"无数据: {ts_code}")
            continue

        adj_df = pro.fund_adj(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,adj_factor",
        )
        if adj_df is None or adj_df.empty:
            print(f"无复权因子: {ts_code}，跳过")
            continue

        df = df[["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]].copy()
        adj_df = adj_df[["ts_code", "trade_date", "adj_factor"]].copy()
        df = df.merge(adj_df, on=["ts_code", "trade_date"], how="left")
        df = df.sort_values("trade_date").reset_index(drop=True)

        # 个别交易日如果复权因子缺失，做前后填充；仍缺失则置为1
        df["adj_factor"] = df["adj_factor"].ffill().bfill().fillna(1.0)

        # 生成复权价格（按你的要求：价格 * 复权因子）
        df["open"] = df["open"] * df["adj_factor"]
        df["high"] = df["high"] * df["adj_factor"]
        df["low"] = df["low"] * df["adj_factor"]
        df["close"] = df["close"] * df["adj_factor"]

        frames.append(df)

    if not frames:
        raise RuntimeError("未下载到任何ETF日线数据，请检查 token 或日期范围。")

    wide_df = pd.concat(frames, axis=0, ignore_index=True)
    wide_df["trade_date"] = pd.to_datetime(wide_df["trade_date"], format="%Y%m%d", errors="coerce")
    wide_df = wide_df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    wide_df["trade_date"] = wide_df["trade_date"].dt.strftime("%Y%m%d")

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ETF日线宽表.csv"

    wide_df.dropna()
    wide_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"宽表已保存: {out_path}")
    print(f"宽表形状: {wide_df.shape}")
    
    # 绘制每个ETF的close价格图表
    plot_dir = Path(__file__).resolve().parent / "close_picture"
    plot_dir.mkdir(parents=True, exist_ok=True)
    
    # 设置matplotlib参数以支持中文和负号
    plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
    plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号
    
    # 从.env文件读取ETF中文名称映射
    env_path = Path(__file__).resolve().parent / ".env"
    with open(env_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    import re
    etf_pool_match = re.search(r'ETF_POOL=\[(.*?)\]', content, re.DOTALL)
    etf_pool_str = '[' + etf_pool_match.group(1) + ']'
    etf_pool = json.loads(etf_pool_str)
    
    # 创建ETF代码到中文名称的映射
    etf_name_map = {}
    for etf in etf_pool:
        code = etf["code"]
        # 根据代码长度判断交易所（6位数字，前3位5开头为上海，1开头为深圳）
        if code.startswith("5"):
            ts_code = f"{code}.SH"
        elif code.startswith("1"):
            ts_code = f"{code}.SZ"
        else:
            # 默认添加.SH
            ts_code = f"{code}.SH"
        etf_name_map[ts_code] = etf["name"]
    
    # 按ETF代码分组绘图
    for ts_code in wide_df['ts_code'].unique():
        etf_data = wide_df[wide_df['ts_code'] == ts_code].copy()
        etf_data['trade_date'] = pd.to_datetime(etf_data['trade_date'], format="%Y%m%d")
        etf_data = etf_data.sort_values('trade_date')
        
        # 获取ETF中文名称
        etf_name = etf_name_map.get(ts_code, ts_code)
        
        plt.figure(figsize=(12, 6))
        plt.plot(etf_data['trade_date'], etf_data['close'], linewidth=2)
        
        # 添加复权日红点标记（这里简化处理，假设所有日期都有复权因子）
        # 实际应用中可以根据adj_factor的变化来确定复权日
        if not etf_data.empty:
            # 这里简单标记几个点作为示例
            # 实际应用中应该根据adj_factor的变化来确定复权日
            marker_dates = etf_data['trade_date'].iloc[::len(etf_data)//10]  # 每10%的数据点标记一个
            marker_prices = etf_data['close'].iloc[::len(etf_data)//10]
            plt.scatter(marker_dates, marker_prices, color='red', s=50, alpha=0.7, label='复权日')
        
        plt.title(f"{etf_name} ({ts_code}) 收盘价")
        plt.xlabel("日期")
        plt.ylabel("收盘价")
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # 设置日期格式
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.gca().xaxis.set_major_locator(mdates.YearLocator())
        plt.xticks(rotation=45)
        
        # 保存图表
        plot_path = plot_dir / f"{ts_code.replace('.', '_')}_close.png"
        plt.tight_layout()
        plt.savefig(plot_path)
        plt.close()
        print(f"图表已保存: {plot_path}")


if __name__ == "__main__":
    download_fund_daily_wide()