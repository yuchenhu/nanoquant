"""市场×月 情绪/状态底表（regime 底表，五维度专业设计）。

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
biz_date_col：trade_date（月末交易日）
write_mode：upsert

================================ 设计原则 ================================
1. 五维度专业市场状态模型（私募业界标准）：价 / 量 / 波 / 估值 / 资金。
   每个维度包含「指数自身」+「成分分布」双视角，辨真假牛/结构性行情。
2. 极简但非入门：不堆砌指标，但涵盖最关键的 regime 判据
   （pct_above_ma60 vs ma250 真假牛、turnover_rate_median 量能、PE 5y 分位）。
3. 无未来函数：所有列截至本月末"当时可知"的回看窗口统计。
4. 维度（dimension_type / dimension_value）：
   - 'all'   全A          —— 全局 regime + 全市场独有（北向/两融/涨停家数）
   - 'index' 上证50/沪深300/中证500/中证1000/中证2000 —— 风格维度 regime
5. 衍生列同表存放其原始分量（便于溯源+下游验证）。

================================ 五维度 36 列 ================================
价(12)   : idx_close, ma60/120/250, idx_ret_1m/3m/12m, profit_ratio,
           up_down_ratio, pct_above_ma60, pct_above_ma250, limit_up_count
量(6)    : idx_amount, turnover_rate_median, amount_pct_3m, amount_pct_1y,
           amount_gini (成交额Gini系数，全A + 各指数均有意义)
波(6)    : idx_volatility_20, idx_volatility_60, max_drawdown_1y,
           avg_correlation (成分股截面平均相关系数), cross_sectional_vol (成分股月收益截面标准差),
           downside_vol_ratio (下行半方差/总方差，熊市不对称性)
估值(6)  : pe_ttm_median, pb_median, pe_pct_5y, pb_pct_5y,
           pe_dispersion (PE 75/25分位比，定价分歧度), pb_pe_divergence (PE分位-PB分位，盈利周期位置)
资金(7)  : north_money, margin_balance (全A独有);
           net_inflow_ratio, inflow_direction_pct, inflow_stability, inflow_breadth, institutional_pct (各维度)

================================ 上游来源 ================================
- 指数自身：index_daily
- 成分归属：panel_index_membership_monthly（月末成分，来自 index_weight）
- 成分分布/估值：stock_daily + stock_daily_basic（过滤成分）
- 资金：moneyflow_hsgt（北向）+ margin（两融）+ moneyflow（主力）
- 情绪极值：limit_list_d（涨停家数，数据始于 2020）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MarketSentimentMonthlyCalculator(PanelCalculator):
    """市场×月 情绪/状态底表（五维度 36 列，index 5 指数 + all 全A）。"""

    table_name = "market_sentiment_monthly"
    primary_keys = ["trade_date", "dimension_type", "dimension_value"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    # ── 五维度 36 列（精简但专业，不堆砌、不科普）──
    output_schema = {
        "trade_date": "string",
        "dimension_type": "string",
        "dimension_value": "string",

        # ===== 价(12) =====
        "idx_close": "float",
        "ma60": "float",
        "ma120": "float",
        "ma250": "float",
        "idx_ret_1m": "float",
        "idx_ret_3m": "float",
        "idx_ret_12m": "float",
        "profit_ratio": "float",
        "up_down_ratio": "float",
        "pct_above_ma60": "float",
        "pct_above_ma250": "float",
        "limit_up_count": "int",

        # ===== 量(6) =====
        "idx_amount": "float",
        "turnover_rate_median": "float",
        "amount_pct_3m": "float",
        "amount_pct_1y": "float",
        "amount_gini": "float",

        # ===== 波(6) =====
        "idx_volatility_20": "float",     # 20日年化波动率: 短期风险水平，急涨急跌时先跳
        "idx_volatility_60": "float",     # 60日年化波动率: 中期风险水平，vol crush(低位)预示方向性行情来临
        "max_drawdown_1y": "float",       # 1年最大回撤: 路径上的尾部风险，比volat更直接反映"已经亏了多少"
        "avg_correlation": "float",        # 成分股截面平均相关: 高=系统性(同涨同跌/宏观驱动)，低=结构性(选股环境)
        "cross_sectional_vol": "float",    # 成分股月收益截面标准差: 高=个股分化大(行业轮动期)，低=齐涨齐跌(趋势市)
        "downside_vol_ratio": "float",     # 指数日收益 std(跌日)/std(涨日): >1.2=恐慌不对称(跌比涨更剧烈/熊市)，<0.8=逼空

        # ===== 估值(6) =====
        "pe_ttm_median": "float",          # 成分股PE中位数: 权重畸变免疫的估值水平
        "pb_median": "float",              # 成分股PB中位数
        "pe_pct_5y": "float",              # PE中位数 5年历史分位: "历史来看贵不贵"
        "pb_pct_5y": "float",              # PB中位数 5年历史分位
        "pe_dispersion": "float",          # PE 75分位/25分位: 成分股定价分歧度，高=市场对不同股票定价分歧大(周期底部/泡沫末期)
        "pb_pe_divergence": "float",       # PE分位 - PB分位: 盈利周期位置，正=PE分位高PB分位低(盈利暂时低迷/周期底部)

        # ===== 资金(7) =====
        # 全A独有: north_money/margin_balance; 各维度: 以下5列
        "north_money": "float",             # 北向月度净流入(全A独有): 海外资金态度
        "margin_balance": "float",          # 两融余额月末值(全A独有): 杠杆情绪
        "net_inflow_ratio": "float",        # 月度净主动买/总主动成交: 资金方向强度(tushare net_mf=主动买-主动卖)
        "inflow_direction_pct": "float",    # 日净主动买>0天数/月交易天数: 买方急躁的持续性，>0.6=持续追价
        "inflow_stability": "float",        # mean(日净)/std(日净): 资金流平稳度，高=均匀(机构)，低=大起大落(情绪化)
        "inflow_breadth": "float",          # 月度净流入>0的股票占比: 资金广度，高=撒胡椒面(普涨基础)，低=定向灌溉
        "institutional_pct": "float",       # (特大单+大单)成交/总成交: 机构参与度代理，绝对值有偏但时序突变有信号
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._code_to_dim: dict[str, str] = {
            "000016.SH": "上证50",
            "000300.SH": "沪深300",
            "000905.SH": "中证500",
            "000852.SH": "中证1000",
            "932000.CSI": "中证2000",
        }
        self._all_index_code = "000985.CSI"
        self._index_codes = list(self._code_to_dim.keys())
        self.logger.info(
            "MarketSentimentMonthlyCalculator 初始化（%d 指数 + all 全A, 五维度 29 列）",
            len(self._index_codes),
        )

    # ===== 通用辅助 =====

    @staticmethod
    def _monthly_trade_dates(engine, start_date: str, end_date: str) -> list[str]:
        """区间内每月最后交易日列表（yyyy-mm-dd 字符串）。"""
        from core.dates import get_monthly_last_tradedate
        sy = int(start_date[:4])
        ey = int(end_date[:4])
        all_dates = get_monthly_last_tradedate(engine, sy, ey)
        result = []
        for d in all_dates:
            if start_date[:6] <= d[:6] <= end_date[:6]:
                result.append(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        return result

    @staticmethod
    def _isin_month(dates: pd.Series, year_month: str) -> pd.Series:
        return dates.astype(str).str[:7] == year_month

    @staticmethod
    def _ma(df_sorted: pd.DataFrame, window: int) -> pd.Series:
        return df_sorted["close"].rolling(window, min_periods=window).mean()

    # ===== 取数 =====

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        if not start_date or not end_date:
            self.logger.warning("get_data 需要 start_date/end_date")
            return pd.DataFrame()
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

        upstream: dict[str, pd.DataFrame] = {}
        codelist = ",".join(f"'{c}'" for c in self._index_codes + [self._all_index_code])

        # 1. 指数日线
        upstream["idx"] = pd.read_sql(
            f"SELECT ts_code, trade_date, `close`, amount FROM index_daily "
            f"WHERE ts_code IN ({codelist}) AND trade_date >= '{sd}' AND trade_date <= '{ed}' "
            f"ORDER BY ts_code, trade_date",
            self.engine,
        )
        # 2. 月末成分
        upstream["member"] = pd.read_sql(
            f"SELECT trade_date, ts_code, index_code FROM panel_index_membership_monthly "
            f"WHERE trade_date >= '{sd}' AND trade_date <= '{ed}' AND index_code IN ({codelist})",
            self.engine,
        )
        # 3. 个股行情 + 指标（前推一年算分位/MA）
        read_start = f"{int(sd[:4]) - 1}-01-01"
        upstream["stk"] = pd.read_sql(
            f"SELECT d.ts_code, d.trade_date, d.`close`, d.amount, "
            f"  b.pe_ttm, b.pb, b.turnover_rate "
            f"FROM stock_daily d LEFT JOIN stock_daily_basic b "
            f"  ON d.ts_code=b.ts_code AND d.trade_date=b.trade_date "
            f"WHERE d.trade_date >= '{read_start}' AND d.trade_date <= '{ed}'",
            self.engine,
        )
        # 4. 全市场资金
        upstream["hsgt"] = pd.read_sql(
            f"SELECT trade_date, north_flow FROM moneyflow_hsgt "
            f"WHERE trade_date >= '{sd}' AND trade_date <= '{ed}'",
            self.engine,
        )
        upstream["margin"] = pd.read_sql(
            f"SELECT trade_date, exchange_id, rzrqye FROM margin "
            f"WHERE trade_date >= '{sd}' AND trade_date <= '{ed}'",
            self.engine,
        )
        upstream["mf"] = pd.read_sql(
            f"SELECT ts_code, trade_date, net_mf_amount, "
            f"  buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount, "
            f"  buy_md_amount, sell_md_amount, buy_sm_amount, sell_sm_amount "
            f"FROM moneyflow "
            f"WHERE trade_date >= '{sd}' AND trade_date <= '{ed}'",
            self.engine,
        )
        upstream["limit"] = pd.read_sql(
            f"SELECT trade_date, ts_code FROM limit_list_d "
            f"WHERE trade_date >= '{sd}' AND trade_date <= '{ed}'",
            self.engine,
        )
        # 5. 月末交易日列表
        upstream["month_ends"] = self._monthly_trade_dates(self.engine, start_date, end_date)
        self.logger.info(
            "取数完成: idx=%d member=%d stk=%d hsgt=%d margin=%d mf=%d limit=%d 月末=%d",
            len(upstream["idx"]), len(upstream["member"]),
            len(upstream["stk"]), len(upstream["hsgt"]), len(upstream["margin"]),
            len(upstream["mf"]), len(upstream["limit"]), len(upstream["month_ends"]),
        )
        return pd.DataFrame({"__upstream__": [upstream]})

    # ===== 计算 =====

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data is None or data.empty:
            return pd.DataFrame()
        try:
            upstream = data.iloc[0]["__upstream__"]
        except (KeyError, IndexError):
            self.logger.warning("取数结果为空，跳过计算")
            return pd.DataFrame()

        idx_all   = upstream["idx"]
        member    = upstream["member"]
        stk_all   = upstream["stk"]
        hsgt      = upstream["hsgt"]
        margin_df = upstream["margin"]
        mf        = upstream["mf"]
        limit     = upstream["limit"]
        month_ends = upstream["month_ends"]

        if not month_ends:
            return pd.DataFrame()

        for df in [idx_all, member, stk_all, hsgt, margin_df, mf, limit]:
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")

        # 预计算个股 MA（一次算、多列复用）
        stk_all_sorted = stk_all.sort_values(["ts_code", "trade_date"])
        stk_all_sorted["_ma60"] = (
            stk_all_sorted.groupby("ts_code")["close"]
            .rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
        )
        stk_all_sorted["_ma250"] = (
            stk_all_sorted.groupby("ts_code")["close"]
            .rolling(250, min_periods=250).mean().reset_index(level=0, drop=True)
        )

        rows = []
        for me in month_ends:
            yyyymm = me[:7]
            me_dt = pd.Timestamp(me)

            # dimension='all'
            all_row = self._compute_one_dim(
                "all", "全A", me, me_dt, yyyymm,
                idx_all, stk_all, stk_all_sorted,
                hsgt, margin_df, mf, limit,
            )
            rows.append(all_row)

            # dimension='index' × 5
            for code, name in self._code_to_dim.items():
                idx_data = idx_all[idx_all["ts_code"] == code]
                if idx_data.empty:
                    continue
                mem = member[(member["index_code"] == code) & (member["trade_date"] == me_dt)]
                if mem.empty:
                    continue
                member_codes = set(mem["ts_code"].unique())
                stk_dim = stk_all[stk_all["ts_code"].isin(member_codes)]
                stk_dim_sorted = stk_all_sorted[stk_all_sorted["ts_code"].isin(member_codes)]
                index_row = self._compute_one_dim(
                    "index", name, me, me_dt, yyyymm,
                    idx_data, stk_dim, stk_dim_sorted,
                    hsgt, margin_df, mf, limit,
                )
                rows.append(index_row)

        if not rows:
            return pd.DataFrame()
        result = pd.DataFrame(rows)
        col_order = [c for c in self.output_schema if c in result.columns]
        return result[col_order]

    def _compute_one_dim(
        self, dim_type: str, dim_val: str, me: str, me_dt: pd.Timestamp, yyyymm: str,
        idx_data: pd.DataFrame,
        stk_dim: pd.DataFrame, stk_sorted: pd.DataFrame,
        hsgt: pd.DataFrame, margin_df: pd.DataFrame, mf: pd.DataFrame, limit: pd.DataFrame,
    ) -> dict:
        r: dict = {"trade_date": me, "dimension_type": dim_type, "dimension_value": dim_val}
        is_all = dim_type == "all"

        if is_all:
            idx_for_trend = idx_data[idx_data["ts_code"] == self._all_index_code]
        else:
            idx_for_trend = idx_data

        idx_sorted = idx_for_trend.sort_values("trade_date") if not idx_for_trend.empty else pd.DataFrame()
        hist = idx_sorted[idx_sorted["trade_date"] <= me_dt] if not idx_sorted.empty else pd.DataFrame()

        month_stk = stk_dim[self._isin_month(stk_dim["trade_date"], yyyymm)] if not stk_dim.empty else pd.DataFrame()

        # ══════════════ 价(12) ══════════════

        if not hist.empty and len(hist) >= 2:
            r["idx_close"] = float(hist["close"].iloc[-1])
            r["ma60"]  = float(hist["close"].tail(60).mean())   if len(hist) >= 60  else None
            r["ma120"] = float(hist["close"].tail(120).mean())  if len(hist) >= 120 else None
            r["ma250"] = float(hist["close"].tail(250).mean())  if len(hist) >= 250 else None
            c = hist["close"]
            if len(c) >= 21:   r["idx_ret_1m"]  = float(c.iloc[-1] / c.iloc[-21] - 1)
            if len(c) >= 63:   r["idx_ret_3m"]  = float(c.iloc[-1] / c.iloc[-63] - 1)
            if len(c) >= 252:  r["idx_ret_12m"] = float(c.iloc[-1] / c.iloc[-252] - 1)
        else:
            for col in ["idx_close","ma60","ma120","ma250","idx_ret_1m","idx_ret_3m","idx_ret_12m"]:
                r[col] = None

        # 赚钱效应 + 涨跌比
        if not month_stk.empty and "close" in month_stk.columns:
            grp = month_stk.groupby("ts_code")["close"].agg(["first", "last"])
            grp["ret"] = grp["last"] / grp["first"] - 1
            up_n = int((grp["ret"] > 0).sum())
            down_n = int((grp["ret"] < 0).sum())
            r["profit_ratio"] = float(up_n / (up_n + down_n)) if (up_n + down_n) > 0 else None
            r["up_down_ratio"] = float(up_n / down_n) if down_n > 0 else (float("inf") if up_n > 0 else None)
        else:
            r["profit_ratio"] = None; r["up_down_ratio"] = None

        # MA 占比（真假牛判据核心）
        if not stk_sorted.empty and "_ma60" in stk_sorted.columns:
            eom = stk_sorted[stk_sorted["trade_date"] <= me_dt].groupby("ts_code").tail(1)
            above_ma60  = (eom["close"] > eom["_ma60"]).sum()
            above_ma250 = (eom["close"] > eom["_ma250"]).sum()
            valid_ma = eom.dropna(subset=["_ma60", "_ma250"])
            r["pct_above_ma60"]  = float(above_ma60 / len(valid_ma))  if len(valid_ma) > 0 else None
            r["pct_above_ma250"] = float(above_ma250 / len(valid_ma)) if len(valid_ma) > 0 else None
        else:
            r["pct_above_ma60"] = None; r["pct_above_ma250"] = None

        r["limit_up_count"] = (
            int(limit[self._isin_month(limit["trade_date"], yyyymm)].shape[0]) if is_all else None
        )

        # ══════════════ 量(7) ══════════════

        if not idx_sorted.empty and "amount" in idx_sorted.columns:
            r["idx_amount"] = float(idx_sorted[self._isin_month(idx_sorted["trade_date"], yyyymm)]["amount"].sum() or 0)

        if not month_stk.empty and "turnover_rate" in month_stk.columns:
            tr = month_stk["turnover_rate"].dropna()
            r["turnover_rate_median"] = float(tr.median()) if len(tr) > 0 else None
        else:
            r["turnover_rate_median"] = None

        # 成交额分位（3m + 1y）
        if not idx_sorted.empty and "amount" in idx_sorted.columns:
            daily_amt = idx_sorted.set_index("trade_date")["amount"].resample("D").sum().dropna()
            if len(daily_amt) > 0 and r["idx_amount"] is not None:
                def _pct(series, days):
                    w = series[series.index > me_dt - pd.Timedelta(days=days)]
                    w = w[w.index <= me_dt]
                    return float((w < r["idx_amount"]).sum() / len(w)) if len(w) > 0 else None
                r["amount_pct_3m"] = _pct(daily_amt, 90)
                r["amount_pct_1y"] = _pct(daily_amt, 365)
            else:
                r["amount_pct_3m"] = None; r["amount_pct_1y"] = None
        else:
            r["amount_pct_3m"] = None; r["amount_pct_1y"] = None

        # 成交额 Gini 系数（全A + 各指数均有意义）
        if not month_stk.empty and "amount" in month_stk.columns:
            amt = month_stk.groupby("ts_code")["amount"].sum().sort_values(ascending=True)
            if len(amt) >= 5:
                n = len(amt)
                rank = np.arange(1, n + 1)
                r["amount_gini"] = float(2 * np.dot(rank, amt.values) / (n * amt.sum()) - (n + 1) / n)
            else:
                r["amount_gini"] = None
        else:
            r["amount_gini"] = None

        # ══════════════ 波(6) ══════════════
        # 物理意义分层:
        #   [指数自身] idx_volatility_20/60: 时间序列风险 — "市场自己稳不稳"
        #   [指数自身] max_drawdown_1y: 尾部风险 — "已经发生的最大亏损"
        #   [指数自身] downside_vol_ratio: 不对称性 — "跌的时候比涨更猛吗"
        #   [个股截面] avg_correlation: 同向度 — "大家同涨同跌吗"
        #   [个股截面] cross_sectional_vol: 分化度 — "不同股票赚多赚少差多少"

        r["idx_volatility_20"] = None
        r["idx_volatility_60"] = None
        r["max_drawdown_1y"] = None
        r["avg_correlation"] = None
        r["cross_sectional_vol"] = None
        r["downside_vol_ratio"] = None

        # 指数自身：波动率 + 回撤 + 下行不对称
        if not hist.empty and len(hist) >= 20:
            r20 = hist["close"].tail(20).pct_change().dropna()
            r["idx_volatility_20"] = float(r20.std() * (252 ** 0.5)) if len(r20) > 1 else None
        if not hist.empty and len(hist) >= 60:
            r60 = hist["close"].tail(60).pct_change().dropna()
            r["idx_volatility_60"] = float(r60.std() * (252 ** 0.5)) if len(r60) > 1 else None
            # downside_vol_ratio: 指数当月日收益 std(跌日)/std(涨日)
            # 物理意义: 市场下行波动不对称性。>1.2 = 跌日振幅比涨日宽20%以上，
            # 表明恐慌抛售+抄底反抽反复出现，是熊市/转向前信号。
            # std(涨日)/std(跌日) 天然互补，不需要单独 upside 列。
            idx_month = hist[self._isin_month(hist["trade_date"], yyyymm)]
            if len(idx_month) >= 10:
                idx_rets = idx_month["close"].pct_change().dropna()
                neg = idx_rets[idx_rets < 0]
                pos = idx_rets[idx_rets > 0]
                if len(neg) >= 3 and len(pos) >= 3:
                    r["downside_vol_ratio"] = float(neg.std() / pos.std())
            year = hist[hist["trade_date"] > me_dt - pd.Timedelta(days=365)]
            if len(year) >= 2:
                cummax = year["close"].cummax()
                dd = (year["close"] - cummax) / cummax
                r["max_drawdown_1y"] = float(dd.min())

        # 截面分化：avg_correlation + cross_sectional_vol（个股层面）
        if not month_stk.empty and "close" in month_stk.columns:
            stk_ret = month_stk.sort_values(["ts_code", "trade_date"]).copy()
            stk_ret["_ret"] = stk_ret.groupby("ts_code")["close"].pct_change()
            stk_ret = stk_ret.dropna(subset=["_ret"])
            if len(stk_ret) >= 20:
                # avg_correlation: 成分股截面平均相关系数
                # 物理意义: 个股日收益的"同向程度"。高 = 大家一起涨跌(系统性/宏观驱动)，
                # 低 = 此消彼长(结构性/选股有空间)。CBOE 隐含相关系数(KCJ)同源公式:
                #   rho ~ Var(等权组合日收益) / (mean(个股日收益std))^2
                # 推导: Var(R_ew) = sigma^2/N + (N-1)/N * rho * sigma^2, N大时 ~ rho * sigma^2
                daily_ew = stk_ret.groupby("trade_date")["_ret"].mean()
                var_ew = daily_ew.var()
                std_i_mean = stk_ret.groupby("ts_code")["_ret"].std().mean()
                if std_i_mean and std_i_mean > 0:
                    r["avg_correlation"] = float(var_ew / (std_i_mean ** 2))

                # cross_sectional_vol: 成分股月收益截面标准差
                # 物理意义: 不同股票这个月"赚多赚少"的分散程度。高 = 有的暴涨有的暴跌
                # (行业轮动激烈)，低 = 大家收益差不多(普涨普跌/趋势市无分化)。
                # 实证(Connolly & Stivers 2003): cs_vol 常领先于时间序列波动率跳升。
                monthly_ret = stk_ret.groupby("ts_code")["_ret"].apply(
                    lambda x: (1 + x).prod() - 1
                )
                if len(monthly_ret) >= 5:
                    r["cross_sectional_vol"] = float(monthly_ret.std())

        # ══════════════ 估值(6) ══════════════
        # 物理意义分层:
        #   pe_ttm_median / pb_median: "现在多贵" — 成分股中位数，权重畸变免疫
        #   pe_pct_5y / pb_pct_5y: "历史来看多贵" — 当前中位数在5年历史的分位
        #   pe_dispersion: "定价分歧大吗" — PE 75/25分位比，高=市场对不同股票看法分裂
        #   pb_pe_divergence: "盈利在周期什么位置" — PE分位-PB分位，正=盈利暂时低迷(周期底部)

        r["pe_ttm_median"] = None
        r["pb_median"] = None
        r["pe_pct_5y"] = None
        r["pb_pct_5y"] = None
        r["pe_dispersion"] = None
        r["pb_pe_divergence"] = None

        if not month_stk.empty and "pe_ttm" in month_stk.columns:
            pe = month_stk["pe_ttm"].dropna()
            pb = month_stk["pb"].dropna()
            r["pe_ttm_median"] = float(pe.median()) if len(pe) > 0 else None
            r["pb_median"]     = float(pb.median()) if len(pb) > 0 else None

            # pe_dispersion: PE 75分位 / 25分位（定价分歧度）
            if len(pe) >= 10:
                q75, q25 = pe.quantile(0.75), pe.quantile(0.25)
                r["pe_dispersion"] = float(q75 / q25) if q25 > 0 else None

            if not stk_dim.empty and "pe_ttm" in stk_dim.columns:
                hist5y = stk_dim[
                    (stk_dim["trade_date"] > me_dt - pd.Timedelta(days=365 * 5))
                    & (stk_dim["trade_date"] <= me_dt)
                ]
                if not hist5y.empty and r["pe_ttm_median"] is not None:
                    hist5y["_ym"] = hist5y["trade_date"].astype(str).str[:7]
                    pe_s = hist5y.groupby("_ym")["pe_ttm"].median().dropna().sort_index()
                    pb_s = hist5y.groupby("_ym")["pb"].median().dropna().sort_index()
                    r["pe_pct_5y"] = float((pe_s < r["pe_ttm_median"]).sum() / len(pe_s)) if len(pe_s) > 0 else None
                    r["pb_pct_5y"] = float((pb_s < r["pb_median"]).sum() / len(pb_s)) if len(pb_s) > 0 else None
                    # pb_pe_divergence: PE分位 - PB分位（盈利周期位置）
                    if r["pe_pct_5y"] is not None and r["pb_pct_5y"] is not None:
                        r["pb_pe_divergence"] = float(r["pe_pct_5y"] - r["pb_pct_5y"])

        # ══════════════ 资金(7) ══════════════
        # 物理意义分层:
        #   north_money / margin_balance: 全A独有 — 外资态度 + 杠杆情绪
        #   net_inflow_ratio: 资金方向 — 月度净主动买/总主动成交
        #   inflow_direction_pct: 方向持续性 — 买方急躁是否天天如此
        #   inflow_stability: 资金平稳度 — 均匀流入(机构) vs 大起大落(情绪化)
        #   inflow_breadth: 资金广度 — 钱撒胡椒面还是定向灌溉
        #   institutional_pct: 机构参与度代理 — 大单占比的时序突变
        # tushare net_mf = 主动买金额 - 主动卖金额, 测的是"谁在付spread/谁更急躁"

        r["north_money"] = None
        r["margin_balance"] = None
        r["net_inflow_ratio"] = None
        r["inflow_direction_pct"] = None
        r["inflow_stability"] = None
        r["inflow_breadth"] = None
        r["institutional_pct"] = None

        # 全A独有: 北向 + 两融
        if is_all:
            m_hsgt = hsgt[self._isin_month(hsgt["trade_date"], yyyymm)]
            r["north_money"] = float(m_hsgt["north_flow"].sum()) if not m_hsgt.empty and "north_flow" in m_hsgt.columns else None
            m_margin = margin_df[self._isin_month(margin_df["trade_date"], yyyymm)]
            r["margin_balance"] = float(m_margin["rzrqye"].iloc[-1]) if not m_margin.empty and "rzrqye" in m_margin.columns else None

        # 各维度: 主动买卖资金流（先日聚合，再月统计）
        if not mf.empty and "ts_code" in mf.columns and "net_mf_amount" in mf.columns:
            if not month_stk.empty:
                member_codes = set(month_stk["ts_code"].unique())
                mf_dim = mf[mf["ts_code"].isin(member_codes)]
            else:
                mf_dim = mf
            mf_month = mf_dim[self._isin_month(mf_dim["trade_date"], yyyymm)]
            if len(mf_month) >= 100:
                # 日聚合
                dgrp = mf_month.groupby("trade_date")
                daily_net = dgrp["net_mf_amount"].sum()
                daily_total = dgrp[["buy_elg_amount","sell_elg_amount","buy_lg_amount","sell_lg_amount",
                                     "buy_md_amount","sell_md_amount","buy_sm_amount","sell_sm_amount"]].sum().sum(axis=1)
                daily_inst = dgrp[["buy_elg_amount","sell_elg_amount","buy_lg_amount","sell_lg_amount"]].sum().sum(axis=1)

                # net_inflow_ratio: 月度净主动买 / 月度总主动成交
                tot_net = daily_net.sum()
                tot_amt = daily_total.sum()
                r["net_inflow_ratio"] = float(tot_net / tot_amt) if tot_amt > 0 else None

                # inflow_direction_pct: 买方急躁的持续性
                if len(daily_net) >= 5:
                    r["inflow_direction_pct"] = float((daily_net > 0).sum() / len(daily_net))

                # inflow_stability: mean/std (日净的平稳度)
                if len(daily_net) >= 5 and daily_net.std() > 0:
                    r["inflow_stability"] = float(daily_net.mean() / daily_net.std())

                # inflow_breadth: 月度净流入为正的股票占比
                stock_net = mf_month.groupby("ts_code")["net_mf_amount"].sum()
                if len(stock_net) >= 5:
                    r["inflow_breadth"] = float((stock_net > 0).sum() / len(stock_net))

                # institutional_pct: 大单+特大单 / 总成交
                r["institutional_pct"] = float(daily_inst.sum() / tot_amt) if tot_amt > 0 else None

        return r
