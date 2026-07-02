"""市场×月 情绪/状态底表（regime 底表，五维度专业设计）。

表名：panel_market_sentiment_monthly
主键：trade_date + dimension_type + dimension_value
biz_date_col：trade_date（月末交易日）
write_mode：overwrite + partition_col=trade_date

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
价(11)   : idx_close, ma60/250, idx_ret_1m/3m/12m（日历月对齐，非固定交易日数）,
           profit_ratio, up_down_ratio, pct_above_ma60, pct_above_ma250,
           limit_up_count（全A独有，数据始于2020）
量(6)    : idx_amount, turnover_rate_median, amount_pct_3m, amount_pct_1y, amount_gini
波(6)    : idx_volatility_20, idx_volatility_60, max_drawdown_1y,
           avg_correlation, cross_sectional_vol, downside_vol_ratio
估值(7)  : pe_ttm_median, pb_median, dv_ttm_median（股息率，价值风格估值锚）,
           pe_pct_5y, pb_pct_5y（5年月末抽样，非逐日）, pe_dispersion, pb_pe_divergence
资金(7)  : north_money, margin_balance（全A独有）;
           net_inflow_ratio, inflow_direction_pct, inflow_stability,
           inflow_breadth, institutional_pct

================================ 上游来源 ================================
- 指数自身：index_daily（前溯1年日线，MA/波动率/回撤用日线，ret用月末对齐）
- 成分分布/估值/资金流/成分归属：panel_stock_daily
  · 日线数据前溯1年（MA60/250、换手率、资金流、截面相关）
  · 月末抽样前溯5年（PE/PB/股息率历史分位，仅取60个月末截面，IO减少~20倍）
- 资金（全市场）：moneyflow_hsgt（北向）+ margin（两融）
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
    write_mode = "overwrite"
    partition_col = "trade_date"  # 按月覆盖（同月末删除所有dimension行再批量写入）

    output_schema = {
        "trade_date": "string",
        "dimension_type": "string",
        "dimension_value": "string",

        # ===== 价(11) =====
        "idx_close": "float",
        "ma60": "float", "ma250": "float",
        # idx_ret_*：日历月对齐收益率，非固定交易日数
        #   例：idx_ret_1m = 本月末收盘 / 上月末收盘 - 1
        "idx_ret_1m": "float", "idx_ret_3m": "float", "idx_ret_12m": "float",
        # profit_ratio：月度上涨家数占比，∈[0,1]。0.6=60%股票月内上涨
        # up_down_ratio：上涨家数/下跌家数。与profit_ratio互补：
        #   profit_ratio=0.6可能是60/40(温和偏多,up_down=1.5)或6/4(极端,up_down=1.5)
        #   两者相同因为分子分母等比例缩放；真正的区分在60/40(=1.5) vs 90/10(=9.0)
        "profit_ratio": "float", "up_down_ratio": "float",
        # pct_above_ma60/250：当前价>均线的成分股占比。参与率指标——
        #   指数涨但pct_above低 = 权重股拉指数、多数个股不跟（假牛）
        "pct_above_ma60": "float", "pct_above_ma250": "float",
        "limit_up_count": "int",

        # ===== 量(6) =====
        "idx_amount": "float",
        # turnover_rate_median：成分股月均换手率中位数。高=交投活跃/投机氛围浓
        "turnover_rate_median": "float",
        # amount_pct_3m/1y：当月指数成交额在近3月/1年中的分位，∈[0,1]。
        #   0.9=本月成交额高于90%的历史日，量能异常放大
        "amount_pct_3m": "float", "amount_pct_1y": "float",
        # amount_gini：成分股月成交额Gini系数，∈[0,1]。0=完全均匀，1=完全集中。
        #   高值=资金集中在少数股票（抱团/防御），低值=资金分散（全面行情）
        "amount_gini": "float",

        # ===== 波(6) =====
        # idx_volatility_20/60：指数20/60日年化波动率（std×√252）
        "idx_volatility_20": "float",
        "idx_volatility_60": "float",
        "max_drawdown_1y": "float",  # 近1年最大回撤（%），负值
        # avg_correlation：成分股截面平均相关系数（CBOE KCJ同源公式）。
        #   ρ ≈ Var(等权日收益) / avg(个股日收益方差)。高=系统性风险（同涨同跌），低=个股分化
        "avg_correlation": "float",
        # cross_sectional_vol：成分股月收益截面标准差。衡量个股表现分化度——
        #   高=选股重要（alpha环境好），低=β行情（涨跌靠仓位不靠选股）
        "cross_sectional_vol": "float",
        # downside_vol_ratio：当月日收益下行std / 上行std。>1=下跌波动>上涨（熊市不对称），<1=牛
        "downside_vol_ratio": "float",

        # ===== 估值(7) =====
        "pe_ttm_median": "float", "pb_median": "float",
        # dv_ttm_median：成分股TTM股息率中位数。价值风格的估值锚——
        #   PE低+股息低≠便宜（盈利质量差），PE低+股息高=真便宜
        "dv_ttm_median": "float",
        # pe_pct_5y/pb_pct_5y：当前PE/PB中位数在5年月度序列中的分位，∈[0,1]。
        #   月末抽样（60个点），非逐日（1250个点），精度损失可忽略
        "pe_pct_5y": "float", "pb_pct_5y": "float",
        # pe_dispersion：成分股PE 75/25分位比。衡量市场对"谁更值钱"的分歧程度——
        #   高=定价混乱/结构性分化，低=市场共识强
        "pe_dispersion": "float",
        # pb_pe_divergence：PE分位 - PB分位。>0=盈利好但PE溢价（盈利周期高位），<0=盈利差（低谷）
        "pb_pe_divergence": "float",

        # ===== 资金(7) =====
        "north_money": "float", "margin_balance": "float",
        # net_inflow_ratio：净主动买入 / 总主动成交。>0=主动买盘主导，<0=主动卖盘主导
        "net_inflow_ratio": "float",
        # inflow_direction_pct：日净流入>0的天数占比。高=资金持续偏多，低=方向不明确
        "inflow_direction_pct": "float",
        # inflow_stability：日均净流入 / 日净流入std = 信息比率。高=资金稳定流入，低=进出波动大
        "inflow_stability": "float",
        # inflow_breadth：净流入>0的成分股占比。高=资金广泛流入（全面性），低=资金集中在少数票
        "inflow_breadth": "float",
        # institutional_pct：(特大单+大单)/总主动成交。代理机构参与度——高=机构主导，低=散户主导
        "institutional_pct": "float",
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
        # 指数代码 → panel_stock_daily 的 is_xxx 列名
        self._code_to_is_col: dict[str, str] = {
            "000016.SH": "is_sz50",
            "000300.SH": "is_hs300",
            "000905.SH": "is_zz500",
            "000852.SH": "is_zz1000",
            "932000.CSI": "is_zz2000",
            "000985.CSI": "is_zzqz",
        }
        self.logger.info(
            "初始化: %d 指数 + all 全A, 五维度 36 列",
            len(self._index_codes),
        )

    # ================================================================
    # 通用辅助
    # ================================================================

    @staticmethod
    def _monthly_trade_dates(engine, start_date: str, end_date: str) -> list[str]:
        """区间内每月最后交易日列表（yyyy-mm-dd 字符串）。"""
        from core.dates import get_monthly_last_tradedate
        sy, ey = int(start_date[:4]), int(end_date[:4])
        result = []
        for d in get_monthly_last_tradedate(engine, sy, ey):
            if start_date[:6] <= d[:6] <= end_date[:6]:
                result.append(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        return result

    @staticmethod
    def _isin_month(dates: pd.Series, year_month: str) -> pd.Series:
        return dates.astype(str).str[:7] == year_month

    @staticmethod
    def _prev_ym(yyyymm: str, offset: int, month_ends: list[str]) -> Optional[str]:
        """从 month_ends 列表中取 yyyymm 前 offset 个月的 yyyy-mm。"""
        yms = [me[:7] for me in month_ends]
        try:
            idx = yms.index(yyyymm)
            return yms[idx - offset] if idx - offset >= 0 else None
        except ValueError:
            return None

    # ================================================================
    # get_data：上游取数
    # ================================================================

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        if not start_date or not end_date:
            self.logger.warning("get_data 需要 start_date/end_date")
            return pd.DataFrame()
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

        upstream: dict[str, pd.DataFrame] = {}
        index_code_list = ",".join(f"'{c}'" for c in self._index_codes + [self._all_index_code])
        is_cols = ",".join(self._code_to_is_col.values())
        # 日线前溯1年（MA60/250、波动率、回撤）
        read_start_idx = f"{int(sd[:4]) - 1}-01-01"
        # PE/PB历史分位前溯5年（月末抽样，非逐日）
        read_start_5y = f"{int(sd[:4]) - 5}-01-01"

        # [1/7] 指数日线（前溯一年算 MA/波动率/回撤）
        self.logger.info("[1/7] 取 index_daily (前溯 %s)...", read_start_idx)
        upstream["idx"] = pd.read_sql(
            f"SELECT ts_code, trade_date, `close`, amount FROM index_daily "
            f"WHERE ts_code IN ({index_code_list}) AND trade_date >= '{read_start_idx}' AND trade_date <= '{ed}' "
            f"ORDER BY ts_code, trade_date",
            self.engine,
        )
        self.logger.info("  index_daily: %d 行", len(upstream["idx"]))

        # [2/7] 个股行情日线（前溯1年，MA/换手率/资金流/相关性用）
        self.logger.info("[2/7] 取 panel_stock_daily 日线 (前溯 %s)...", read_start_idx)
        upstream["psd"] = pd.read_sql(
            f"SELECT ts_code, trade_date, `close`, amount, pe_ttm, pb, dv_ttm, turnover_rate, "
            f"  net_mf_amount, "
            f"  buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount, "
            f"  buy_md_amount, sell_md_amount, buy_sm_amount, sell_sm_amount, "
            f"  {is_cols} "
            f"FROM panel_stock_daily "
            f"WHERE trade_date >= '{read_start_idx}' AND trade_date <= '{ed}'",
            self.engine,
        )
        self.logger.info("  panel_stock_daily 日线: %d 行", len(upstream["psd"]))

        # [3/7] 月末交易日列表（计算期 + 前溯5年，用于PE/PB月末抽样）
        self.logger.info("[3/7] 计算月末交易日列表...")
        upstream["month_ends"] = self._monthly_trade_dates(self.engine, start_date, end_date)
        # 前溯5年的月末列表（用于PE/PB分位抽样）
        all_month_ends = self._monthly_trade_dates(
            self.engine, read_start_5y.replace("-", ""), end_date
        )
        upstream["all_month_ends"] = all_month_ends
        self.logger.info("  计算期月末: %d, 含5年前溯: %d",
                         len(upstream["month_ends"]), len(all_month_ends))

        # [4/7] PE/PB/股息率 月末抽样（前溯5年，仅月末截面，IO减少~20倍 vs 逐日）
        me_list = ",".join(f"'{d}'" for d in all_month_ends)
        self.logger.info("[4/7] 取 panel_stock_daily 月末抽样 (前溯5年, %d个截面)...",
                         len(all_month_ends))
        upstream["psd_monthly"] = pd.read_sql(
            f"SELECT ts_code, trade_date, pe_ttm, pb, dv_ttm "
            f"FROM panel_stock_daily "
            f"WHERE trade_date IN ({me_list}) "
            f"ORDER BY ts_code, trade_date",
            self.engine,
        )
        self.logger.info("  psd_monthly: %d 行", len(upstream["psd_monthly"]))

        # [5/7] 北向资金（取整月，非仅月末一天）
        month_start = f"{sd[:7]}-01"
        self.logger.info("[5/7] 取 moneyflow_hsgt (月 %s)...", sd[:7])
        upstream["hsgt"] = pd.read_sql(
            f"SELECT trade_date, north_money FROM moneyflow_hsgt "
            f"WHERE trade_date >= '{month_start}' AND trade_date <= '{ed}'",
            self.engine,
        )
        self.logger.info("  hsgt: %d 行", len(upstream["hsgt"]))

        # [6/7] 两融余额（取整月）
        self.logger.info("[6/7] 取 margin (月 %s)...", sd[:7])
        upstream["margin"] = pd.read_sql(
            f"SELECT trade_date, exchange_id, rzrqye FROM margin "
            f"WHERE trade_date >= '{month_start}' AND trade_date <= '{ed}'",
            self.engine,
        )
        self.logger.info("  margin: %d 行", len(upstream["margin"]))

        # [7/7] 涨跌停（取整月）
        self.logger.info("[7/7] 取 limit_list_d (月 %s)...", sd[:7])
        upstream["limit"] = pd.read_sql(
            f"SELECT trade_date, ts_code FROM limit_list_d "
            f"WHERE trade_date >= '{month_start}' AND trade_date <= '{ed}'",
            self.engine,
        )
        self.logger.info("  limit: %d 行", len(upstream["limit"]))

        return pd.DataFrame({"__upstream__": [upstream]})

    # ================================================================
    # process_data：主流程
    # ================================================================

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data is None or data.empty:
            return pd.DataFrame()
        try:
            upstream = data.iloc[0]["__upstream__"]
        except (KeyError, IndexError):
            self.logger.warning("取数结果为空，跳过计算")
            return pd.DataFrame()

        idx_all      = upstream["idx"]
        psd          = upstream["psd"]
        psd_monthly  = upstream["psd_monthly"]  # 月末抽样 PE/PB/dv，前溯5年
        hsgt         = upstream["hsgt"]
        margin_df    = upstream["margin"]
        limit        = upstream["limit"]
        month_ends   = upstream["month_ends"]
        all_month_ends = upstream["all_month_ends"]

        if not month_ends:
            return pd.DataFrame()

        import time
        t0 = time.time()

        # 统一 trade_date 为 datetime
        for df in [idx_all, psd, psd_monthly, hsgt, margin_df, limit]:
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")

        # ---- 预计算1：个股 MA60/MA250（一次算、所有维度复用） ----
        self.logger.info("预计算个股 MA60/MA250...")
        psd_sorted = psd.sort_values(["ts_code", "trade_date"])
        psd_sorted["_ma60"] = (
            psd_sorted.groupby("ts_code")["close"]
            .rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
        )
        psd_sorted["_ma250"] = (
            psd_sorted.groupby("ts_code")["close"]
            .rolling(250, min_periods=250).mean().reset_index(level=0, drop=True)
        )
        self.logger.info("MA 预计算完成，%.1fs", time.time() - t0)

        # ---- 预计算2：各指数月末收盘价（用于日历月对齐的 idx_ret） ----
        self.logger.info("预计算指数月末收盘价...")
        idx_all["_ym"] = idx_all["trade_date"].dt.strftime("%Y-%m")
        idx_monthly = (
            idx_all.groupby(["ts_code", "_ym"])["close"]
            .last()
            .reset_index()
        )
        # {(ts_code, yyyymm): close} 快速查找
        idx_close_lookup = {
            (row["ts_code"], row["_ym"]): row["close"]
            for _, row in idx_monthly.iterrows()
        }
        self.logger.info("指数月末收盘 lookup: %d 条，%.1fs",
                         len(idx_close_lookup), time.time() - t0)

        # ---- 预计算3：个股月末 PE/PB/dv（用于5年分位） ----
        psd_monthly["_ym"] = psd_monthly["trade_date"].dt.strftime("%Y-%m")

        rows = []
        total_months = len(month_ends)
        for i, me in enumerate(month_ends):
            yyyymm = me[:7]
            me_dt = pd.Timestamp(me)
            self.logger.info("[%d/%d] 计算 %s ...", i + 1, total_months, yyyymm)
            t_month = time.time()

            # --- dimension='all'：成分 = is_zzqz == 1 ---
            is_col_all = "is_zzqz"
            month_mask_all = psd["trade_date"].dt.strftime("%Y-%m") == yyyymm
            all_codes = set(psd.loc[month_mask_all & (psd[is_col_all] == 1), "ts_code"].unique())
            stk_all_dim = psd[psd["ts_code"].isin(all_codes)]
            stk_all_sorted = psd_sorted[psd_sorted["ts_code"].isin(all_codes)]
            all_row = self._compute_dimension(
                "all", "全A", me, me_dt, yyyymm,
                idx_all=idx_all[idx_all["ts_code"] == self._all_index_code],
                stk=stk_all_dim, stk_sorted=stk_all_sorted,
                hsgt=hsgt, margin=margin_df, mf=psd, limit=limit,
                is_all=True,
                idx_close_lookup=idx_close_lookup,
                monthly_pe_pb=psd_monthly,
                month_ends_list=all_month_ends,  # 用全量月末列表（含5年前溯），保证ret回看
            )
            rows.append(all_row)

            # --- dimension='index' × 5：用 is_xxx 列过滤成分 ---
            for code, name in self._code_to_dim.items():
                is_col = self._code_to_is_col.get(code)
                if not is_col or is_col not in psd.columns:
                    continue
                idx_data = idx_all[idx_all["ts_code"] == code]
                if idx_data.empty:
                    continue
                # 只在月末截面上过滤成分（避免 MA 预计算数据被截断）
                month_mask = psd["trade_date"].dt.strftime("%Y-%m") == yyyymm
                member_codes = set(psd.loc[month_mask & (psd[is_col] == 1), "ts_code"].unique())
                if not member_codes:
                    continue
                stk_dim = psd[psd["ts_code"].isin(member_codes)]
                stk_sorted_dim = psd_sorted[psd_sorted["ts_code"].isin(member_codes)]
                index_row = self._compute_dimension(
                    "index", name, me, me_dt, yyyymm,
                    idx_all=idx_data, stk=stk_dim, stk_sorted=stk_sorted_dim,
                    hsgt=hsgt, margin=margin_df, mf=psd, limit=limit,
                    is_all=False,
                    idx_close_lookup=idx_close_lookup,
                    monthly_pe_pb=psd_monthly,
                    month_ends_list=all_month_ends,  # 用全量月末列表（含5年前溯），保证ret回看
                )
                rows.append(index_row)

            self.logger.info("  完成，耗时 %.1fs", time.time() - t_month)

        self.logger.info("%d 个月全部计算完成，总耗时 %.1fs", total_months, time.time() - t0)

        if not rows:
            return pd.DataFrame()
        result = pd.DataFrame(rows)
        col_order = [c for c in self.output_schema if c in result.columns]
        return result[col_order]

    # ================================================================
    # 单维度计算（调度 5 个维度方法）
    # ================================================================

    def _compute_dimension(
        self, dim_type: str, dim_val: str, me: str, me_dt: pd.Timestamp, yyyymm: str,
        idx_all: pd.DataFrame, stk: pd.DataFrame, stk_sorted: pd.DataFrame,
        hsgt: pd.DataFrame, margin: pd.DataFrame, mf: pd.DataFrame, limit: pd.DataFrame,
        is_all: bool,
        idx_close_lookup: dict,         # {(ts_code, yyyymm): close}
        monthly_pe_pb: pd.DataFrame,    # 月末抽样 PE/PB/dv，前溯5年
        month_ends_list: list[str],     # 计算期月末列表
    ) -> dict:
        """计算一个维度（'all' 或 'index'）的全部 36 列。"""
        r: dict = {"trade_date": me, "dimension_type": dim_type, "dimension_value": dim_val}

        # 指数历史序列（用于指数自身指标：MA/波动率/回撤）
        idx_sorted = idx_all.sort_values("trade_date") if not idx_all.empty else pd.DataFrame()
        idx_hist = idx_sorted[idx_sorted["trade_date"] <= me_dt] if not idx_sorted.empty else pd.DataFrame()

        # 当月成分股截面（用于成分分布指标）
        month_stk = stk[self._isin_month(stk["trade_date"], yyyymm)] if not stk.empty else pd.DataFrame()

        # ---- 价(11) ----
        self._compute_price(
            r, idx_hist, idx_sorted, month_stk, stk_sorted,
            me_dt, yyyymm, limit, is_all,
            idx_close_lookup=idx_close_lookup,
            month_ends_list=month_ends_list,
        )

        # ---- 量(6) ----
        self._compute_volume(r, idx_hist, idx_sorted, month_stk, me_dt, yyyymm)

        # ---- 波(6) ----
        self._compute_volatility(r, idx_hist, month_stk, me_dt, yyyymm)

        # ---- 估值(7) ----
        self._compute_valuation(r, month_stk, monthly_pe_pb, me_dt)

        # ---- 资金(7) ----
        self._compute_flow(r, month_stk, hsgt, margin, mf, yyyymm, is_all)

        return r

    # ================================================================
    # 维度方法：价 (11 列)
    # ================================================================

    def _compute_price(
        self, r: dict,
        idx_hist: pd.DataFrame, idx_sorted: pd.DataFrame,
        month_stk: pd.DataFrame, stk_sorted: pd.DataFrame,
        me_dt: pd.Timestamp, yyyymm: str,
        limit: pd.DataFrame, is_all: bool,
        idx_close_lookup: dict,
        month_ends_list: list[str],
    ) -> None:
        """价维度：指数收盘/均线/日历月对齐动量 + 成分涨跌比/MA占比 + 涨停数。"""
        # --- 指数自身：收盘、均线 ---
        idx_code = idx_hist["ts_code"].iloc[0] if not idx_hist.empty and "ts_code" in idx_hist.columns else None
        if not idx_hist.empty and len(idx_hist) >= 2:
            c = idx_hist["close"]
            r["idx_close"] = float(c.iloc[-1])
            r["ma60"]  = float(c.tail(60).mean())   if len(c) >= 60  else None
            r["ma250"] = float(c.tail(250).mean())  if len(c) >= 250 else None
        else:
            for col in ["idx_close", "ma60", "ma250"]:
                r[col] = None

        # --- 指数自身：日历月对齐收益率（非固定交易日数） ---
        # idx_ret_1m = 本月末close / 上月末close - 1
        for offset, key in [(1, "idx_ret_1m"), (3, "idx_ret_3m"), (12, "idx_ret_12m")]:
            prev_ym = self._prev_ym(yyyymm, offset, month_ends_list)
            if prev_ym and idx_code and (idx_code, prev_ym) in idx_close_lookup:
                prev_close = idx_close_lookup[(idx_code, prev_ym)]
                cur_close = r.get("idx_close")
                if cur_close and prev_close and prev_close > 0:
                    r[key] = float(cur_close / prev_close - 1)
                else:
                    r[key] = None
            else:
                r[key] = None

        # --- 成分：赚钱效应 / 涨跌比 ---
        # profit_ratio = 月内上涨家数 / 总家数，∈[0,1]；值越大越偏多
        # up_down_ratio = 上涨家数/下跌家数；与profit_ratio互补区分强度
        #   例：60/40 → profit=0.6, up_down=1.5；90/10 → profit=0.9, up_down=9.0
        if not month_stk.empty and "close" in month_stk.columns:
            grp = month_stk.groupby("ts_code")["close"].agg(["first", "last"])
            grp["ret"] = grp["last"] / grp["first"] - 1
            up = int((grp["ret"] > 0).sum())
            dn = int((grp["ret"] < 0).sum())
            r["profit_ratio"] = float(up / (up + dn)) if (up + dn) > 0 else None
            r["up_down_ratio"] = float(up / dn) if dn > 0 else (float("inf") if up > 0 else None)
        else:
            r["profit_ratio"] = None
            r["up_down_ratio"] = None

        # --- 成分：站上 MA60/MA250 的股票占比（参与率） ---
        # pct_above_ma60：收盘>MA60的成分占比。指数涨+占比低=权重股拉指数、假牛
        # pct_above_ma250：同上，更长周期。ma60>50%+ma250<50%=中期修复但长期偏弱
        if not stk_sorted.empty and "_ma60" in stk_sorted.columns:
            eom = stk_sorted[stk_sorted["trade_date"] <= me_dt].groupby("ts_code").tail(1)
            # 分别用各自valid分母，避免次新股(有ma60无ma250)导致pct>1
            v60 = eom.dropna(subset=["_ma60"])
            v250 = eom.dropna(subset=["_ma250"])
            r["pct_above_ma60"] = (
                float((v60["close"] > v60["_ma60"]).sum() / len(v60))
                if len(v60) > 0 else None
            )
            r["pct_above_ma250"] = (
                float((v250["close"] > v250["_ma250"]).sum() / len(v250))
                if len(v250) > 0 else None
            )
        else:
            r["pct_above_ma60"] = None
            r["pct_above_ma250"] = None

        # --- 全A独有：涨停家数（limit_list_d 始于2020，早年返回0） ---
        r["limit_up_count"] = (
            int(limit[self._isin_month(limit["trade_date"], yyyymm)].shape[0])
            if is_all else None
        )

    # ================================================================
    # 维度方法：量 (6 列)
    # ================================================================

    def _compute_volume(
        self, r: dict,
        idx_hist: pd.DataFrame, idx_sorted: pd.DataFrame,
        month_stk: pd.DataFrame, me_dt: pd.Timestamp, yyyymm: str,
    ) -> None:
        """量维度：指数成交额/分位 + 成分换手率中位数/Gini。"""
        # --- 指数自身：当月成交额（月度加总） ---
        if not idx_sorted.empty:
            r["idx_amount"] = float(
                idx_sorted[self._isin_month(idx_sorted["trade_date"], yyyymm)]["amount"].sum() or 0
            )
        else:
            r["idx_amount"] = None

        # --- 成分：换手率中位数（市场参与热度） ---
        if not month_stk.empty and "turnover_rate" in month_stk.columns:
            tr = month_stk["turnover_rate"].dropna()
            r["turnover_rate_median"] = float(tr.median()) if len(tr) > 0 else None
        else:
            r["turnover_rate_median"] = None

        # --- 指数自身：成交额分位（3个月 / 1年） ---
        # amount_pct_3m = 本月成交额在近3个月月成交额中的分位，∈[0,1]
        #   注意：比较的是月度总额 vs 月度总额，不是月度 vs 日度
        r["amount_pct_3m"] = None
        r["amount_pct_1y"] = None
        if not idx_sorted.empty and "amount" in idx_sorted.columns and r.get("idx_amount"):
            # 先按月汇总成交额
            idx_sorted_copy = idx_sorted.copy()
            idx_sorted_copy["_ym"] = idx_sorted_copy["trade_date"].dt.strftime("%Y-%m")
            monthly_amt = idx_sorted_copy.groupby("_ym")["amount"].sum().sort_index()
            cur_amt = r["idx_amount"]
            yyyymm_str = me_dt.strftime("%Y-%m")

            def _monthly_pct(series: pd.Series, n_months: int) -> Optional[float]:
                """当前月成交额在近n个月月度成交额中的分位"""
                target_ym = f"{me_dt.year}-{me_dt.month:02d}"
                cutoff = (me_dt - pd.DateOffset(months=n_months)).strftime("%Y-%m")
                w = series[(series.index > cutoff) & (series.index <= target_ym)]
                if len(w) < 3:
                    return None
                return float((w < cur_amt).sum() / len(w))

            r["amount_pct_3m"] = _monthly_pct(monthly_amt, 3)
            r["amount_pct_1y"] = _monthly_pct(monthly_amt, 12)

        # --- 成分：成交额 Gini（资金集中度） ---
        # 0=完全均匀分配，1=全部成交额集中在一只股票
        # 高值=抱团/防御（少数票吸走大部分资金），低值=全面行情（资金分散）
        r["amount_gini"] = None
        if not month_stk.empty and "amount" in month_stk.columns:
            amt = month_stk.groupby("ts_code")["amount"].sum().sort_values()
            if len(amt) >= 5:
                n = len(amt)
                rank = np.arange(1, n + 1)
                r["amount_gini"] = float(
                    2 * np.dot(rank, amt.values) / (n * amt.sum()) - (n + 1) / n
                )

    # ================================================================
    # 维度方法：波 (6 列)
    # ================================================================

    def _compute_volatility(
        self, r: dict,
        idx_hist: pd.DataFrame, month_stk: pd.DataFrame,
        me_dt: pd.Timestamp, yyyymm: str,
    ) -> None:
        """波维度：指数波动率/回撤/下行不对称 + 成分截面相关/分化。"""
        for col in ["idx_volatility_20", "idx_volatility_60", "max_drawdown_1y",
                     "avg_correlation", "cross_sectional_vol", "downside_vol_ratio"]:
            r[col] = None

        # --- 指数自身：20/60日年化波动率（std × √252） ---
        if not idx_hist.empty and len(idx_hist) >= 20:
            r20 = idx_hist["close"].tail(20).pct_change().dropna()
            r["idx_volatility_20"] = float(r20.std() * np.sqrt(252)) if len(r20) > 1 else None
        if not idx_hist.empty and len(idx_hist) >= 60:
            r60 = idx_hist["close"].tail(60).pct_change().dropna()
            r["idx_volatility_60"] = float(r60.std() * np.sqrt(252)) if len(r60) > 1 else None

        # --- 指数自身：当月下行波动不对称 (std_跌/std_涨) ---
        # >1 = 下跌日波动大于上涨日（熊市特征，急跌慢涨），<1 = 牛市特征（急涨慢跌）
        if not idx_hist.empty:
            idx_month = idx_hist[self._isin_month(idx_hist["trade_date"], yyyymm)]
            if len(idx_month) >= 10:
                idx_rets = idx_month["close"].pct_change().dropna()
                neg, pos = idx_rets[idx_rets < 0], idx_rets[idx_rets > 0]
                if len(neg) >= 3 and len(pos) >= 3:
                    r["downside_vol_ratio"] = float(neg.std() / pos.std())

        # --- 指数自身：1年最大回撤 ---
        if not idx_hist.empty:
            year = idx_hist[idx_hist["trade_date"] > me_dt - pd.Timedelta(days=365)]
            if len(year) >= 2:
                cummax = year["close"].cummax()
                dd = (year["close"] - cummax) / cummax
                r["max_drawdown_1y"] = float(dd.min())

        # --- 成分截面：平均相关系数 (CBOE KCJ 同源公式) ---
        # ρ ≈ Var(等权日收益) / (avg(个股日收益std))²
        # 高ρ=系统性风险（同涨同跌，β行情），低ρ=个股分化（alpha环境好）
        if not month_stk.empty and "close" in month_stk.columns:
            stk_ret = month_stk.sort_values(["ts_code", "trade_date"]).copy()
            stk_ret["_ret"] = stk_ret.groupby("ts_code")["close"].pct_change()
            stk_ret = stk_ret.dropna(subset=["_ret"])
            if len(stk_ret) >= 20:
                daily_ew = stk_ret.groupby("trade_date")["_ret"].mean()
                std_i_mean = stk_ret.groupby("ts_code")["_ret"].std().mean()
                if std_i_mean and std_i_mean > 0:
                    r["avg_correlation"] = float(daily_ew.var() / (std_i_mean ** 2))

                # --- 成分截面：月收益截面标准差（个股分化度） ---
                # 高=个股表现差异大（选股重要），低=β行情（仓位决定收益）
                monthly_ret = stk_ret.groupby("ts_code")["_ret"].apply(
                    lambda x: (1 + x).prod() - 1
                )
                if len(monthly_ret) >= 5:
                    r["cross_sectional_vol"] = float(monthly_ret.std())

    # ================================================================
    # 维度方法：估值 (7 列)
    # ================================================================

    def _compute_valuation(
        self, r: dict,
        month_stk: pd.DataFrame,
        monthly_pe_pb: pd.DataFrame,  # 月末抽样 PE/PB/dv，前溯5年，含 _ym 列
        me_dt: pd.Timestamp,
    ) -> None:
        """估值维度：成分 PE/PB/股息率 中位数、5年分位（月末抽样）、定价分歧度、盈利周期位置。"""
        for col in ["pe_ttm_median", "pb_median", "dv_ttm_median",
                     "pe_pct_5y", "pb_pct_5y",
                     "pe_dispersion", "pb_pe_divergence"]:
            r[col] = None

        if month_stk.empty or "pe_ttm" not in month_stk.columns:
            return

        pe = month_stk["pe_ttm"].dropna()
        pb = month_stk["pb"].dropna()
        dv = month_stk["dv_ttm"].dropna() if "dv_ttm" in month_stk.columns else pd.Series(dtype=float)

        # 当前估值水平
        r["pe_ttm_median"] = float(pe.median()) if len(pe) > 0 else None
        r["pb_median"]     = float(pb.median()) if len(pb) > 0 else None
        r["dv_ttm_median"] = float(dv.median()) if len(dv) > 0 else None

        # 定价分歧度：PE 75/25分位比
        # >2=贵的和便宜的差距大（市场对"谁值钱"没共识），<1.5=共识强
        if len(pe) >= 10:
            q75, q25 = pe.quantile(0.75), pe.quantile(0.25)
            r["pe_dispersion"] = float(q75 / q25) if q25 > 0 else None

        # 5年历史分位：从月末抽样数据中过滤当前成分股，逐月算中位数后求分位
        # 关键优化：月末抽样（60个点/5年）替代逐日（1250个点），IO减少~20倍
        if (r["pe_ttm_median"] is not None and not monthly_pe_pb.empty
                and not month_stk.empty):
            member_codes = set(month_stk["ts_code"].unique())
            hist = monthly_pe_pb[
                (monthly_pe_pb["trade_date"] <= me_dt)
                & (monthly_pe_pb["ts_code"].isin(member_codes))
            ]
            if not hist.empty:
                pe_monthly = hist.groupby("_ym")["pe_ttm"].median().dropna().sort_index()
                pb_monthly = hist.groupby("_ym")["pb"].median().dropna().sort_index()
                if len(pe_monthly) > 0:
                    r["pe_pct_5y"] = float(
                        (pe_monthly < r["pe_ttm_median"]).sum() / len(pe_monthly)
                    )
                if len(pb_monthly) > 0:
                    r["pb_pct_5y"] = float(
                        (pb_monthly < r["pb_median"]).sum() / len(pb_monthly)
                    )
                # 盈利周期位置：PE分位 - PB分位
                # >0=PE分位高于PB分位（盈利好但市场给PE溢价，周期高位）
                # <0=PE分位低于PB分位（盈利差但PB已调，周期低谷）
                if r["pe_pct_5y"] is not None and r["pb_pct_5y"] is not None:
                    r["pb_pe_divergence"] = float(r["pe_pct_5y"] - r["pb_pct_5y"])

    # ================================================================
    # 维度方法：资金 (7 列)
    # ================================================================

    def _compute_flow(
        self, r: dict,
        month_stk: pd.DataFrame,
        hsgt: pd.DataFrame, margin: pd.DataFrame, mf: pd.DataFrame,
        yyyymm: str, is_all: bool,
    ) -> None:
        """资金维度：北向/两融（全A独有）+ 成分主动买卖资金流方向/平稳度/机构占比。"""
        for col in ["north_money", "margin_balance", "net_inflow_ratio",
                     "inflow_direction_pct", "inflow_stability",
                     "inflow_breadth", "institutional_pct"]:
            r[col] = None

        # --- 全A独有：北向月度净流入 ---
        if is_all:
            m_hsgt = hsgt[self._isin_month(hsgt["trade_date"], yyyymm)]
            if not m_hsgt.empty and "north_money" in m_hsgt.columns:
                r["north_money"] = float(m_hsgt["north_money"].sum())

        # --- 全A独有：两融余额月末值 ---
        if is_all:
            m_margin = margin[self._isin_month(margin["trade_date"], yyyymm)]
            if not m_margin.empty and "rzrqye" in m_margin.columns:
                r["margin_balance"] = float(m_margin["rzrqye"].iloc[-1])

        # --- 成分股主动买卖资金流（先日聚合、再月统计） ---
        if mf.empty or "net_mf_amount" not in mf.columns:
            return

        # 过滤到当月成分股
        if not month_stk.empty:
            mf_dim = mf[mf["ts_code"].isin(set(month_stk["ts_code"].unique()))]
        else:
            mf_dim = mf
        mf_month = mf_dim[self._isin_month(mf_dim["trade_date"], yyyymm)]
        if len(mf_month) < 100:
            return

        # 日聚合：各档位主动买卖金额
        dgrp = mf_month.groupby("trade_date")
        daily_net = dgrp["net_mf_amount"].sum()
        daily_total = dgrp[[
            "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount",
            "buy_md_amount", "sell_md_amount", "buy_sm_amount", "sell_sm_amount",
        ]].sum().sum(axis=1)
        daily_inst = dgrp[[
            "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount",
        ]].sum().sum(axis=1)

        tot_net = daily_net.sum()
        tot_amt = daily_total.sum()

        # net_inflow_ratio：净主动买/总主动成交。>0=主动买盘主导
        r["net_inflow_ratio"] = float(tot_net / tot_amt) if tot_amt > 0 else None

        # inflow_direction_pct：日净流入>0的天数占比。高=资金方向持续偏多
        if len(daily_net) >= 5:
            r["inflow_direction_pct"] = float((daily_net > 0).sum() / len(daily_net))

        # inflow_stability：日均净流入/std = 资金流信息比率。高=稳定流入，低=进出波动大
        if len(daily_net) >= 5 and daily_net.std() > 0:
            r["inflow_stability"] = float(daily_net.mean() / daily_net.std())

        # inflow_breadth：月度净流入>0的成分股占比。高=资金广泛流入（全面性）
        stock_net = mf_month.groupby("ts_code")["net_mf_amount"].sum()
        if len(stock_net) >= 5:
            r["inflow_breadth"] = float((stock_net > 0).sum() / len(stock_net))

        # institutional_pct：(特大单+大单)/总主动成交。代理机构参与度
        r["institutional_pct"] = float(daily_inst.sum() / tot_amt) if tot_amt > 0 else None
