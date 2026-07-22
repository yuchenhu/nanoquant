"""ETF×日 行情宽表（行情 + 复权 + 净值 + 份额 + 维度标签）。

表名：panel_fund_daily（基类自动加 panel_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：overwrite（按 trade_date 覆盖，幂等）

依赖（schedule_compute.json）：
- fund_basic（ETF 基本信息：名称/类型/上市日）
- fund_daily（日行情：OHLCV）
- fund_adj（复权因子）
- fund_nav（净值：单位/累计/复权，merge_asof backward PIT 不穿越）
- fund_share（份额，merge_asof backward PIT 不穿越）

维度标签（来自 config/etf_universe.classify_etf）：
- sw_l1_code / sw_l1_name：申万一级行业（与 panel_stock_daily.l1_code 对齐）
- style_cap：large/mid/small/all
- style_type：blend/growth/value/dividend
- sector_group：周期/消费/金融/成长/稳定（华泰2020五大风格）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from config.etf_universe import classify_etf, extract_index_name
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class FundDailyPanelCalculator(PanelCalculator):
    """ETF×日 行情宽表。

    覆盖：所有场内可交易 ETF（含股票型/债券型/货币型/商品/REITs）。
    宽基/跨境 ETF 的 sw_l1_code 为空，sector_group 根据对标市场标记（港股→"港股"、跨境→"跨境"）。
    """

    table_name = "fund_daily"  # → panel_fund_daily
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"
    output_schema = {
        # ===== 基础标识 =====
        # 基金代码（如 510050.SH）
        "ts_code": "string",
        # 交易日期（yyyy-mm-dd）
        "trade_date": "string",
        # 基金名称（如"华夏上证50ETF"）
        "name": "string",

        # ===== 日行情（来自 fund_daily）=====
        # 开盘价（元/份）
        "open": "float",
        # 最高价（元/份）
        "high": "float",
        # 最低价（元/份）
        "low": "float",
        # 收盘价（元/份）
        "close": "float",
        # 前收盘价（元/份）
        "pre_close": "float",
        # 涨跌额（元/份）= close - pre_close
        "change": "float",
        # 涨跌幅（%）
        "pct_chg": "float",
        # 成交量（手 = 100份）
        "vol": "float",
        # 成交额（千元）
        "amount": "float",
        # 对数收益率 = ln(1 + pct_chg/100)，便于时间序列聚合
        "log_return": "float",
        # 日内均价（元/份）= amount * 1000 / (vol * 100)，用于判断成交方向
        "vwap": "float",

        # ===== 复权（来自 fund_adj）=====
        # 复权因子（同股票 adj_factor，分红拆分后累积调整系数）
        "adj_factor": "float",

        # ===== ETF 特有指标（来自 fund_nav）=====
        # 单位净值（元/份）= 基金总资产/总份额，PIT 不穿越（取最近 nav_date ≤ trade_date 的净值）
        "unit_nav": "float",
        # 累计净值（元/份）= unit_nav + 历史分红再投资积累
        "accum_nav": "float",
        # 复权净值（元/份）= 包含分红拆分的连续净值序列
        "adj_nav": "float",
        # 折溢价率 = close/unit_nav - 1（负=折价（交易价低于净值），正=溢价（交易价高于净值））
        "discount_rate": "float",

        # ===== 规模（来自 fund_share）=====
        # 总份额（万份），PIT 不穿越
        "fd_share": "float",
        # 基金估算规模（亿元）= close * fd_share / 1e4（万份→亿份 × 元/份 → 亿元）
        "fund_size": "float",

        # ===== 状态 =====
        # 上市日期（yyyy-mm-dd）
        "list_date": "string",
        # 已上市自然日数（日历日，非交易日），用于计算"上市时长"因子
        "list_days": "int",
        # 停牌标记（vol=0 且 pct_chg=0 → 1（停牌），否则 0）
        "is_suspend": "int",

        # ===== 基础分类（来自 fund_basic）=====
        # 基金类型（如"股票型"/"债券型"/"货币型"/"REITs"）
        "fund_type": "string",
        # 投资类型（如"被动指数型"/"增强指数型"）
        "invest_type": "string",

        # ===== 维度标签 → 申万一级（来自 config/etf_universe.classify_etf）=====
        # 申万一级行业代码（如 801150.SI），宽基/风格/跨境 ETF 为空
        "sw_l1_code": "string",
        # 申万一级行业名称（如"医药生物"），与 panel_stock_daily.l1_name 对齐
        "sw_l1_name": "string",

        # ===== 维度标签 → 风格（来自 config/etf_universe.classify_etf）=====
        # 市值风格：large(大盘)/mid(中盘)/small(小盘)/all(全市场)/unknown(未分类)
        "style_cap": "string",
        # 风格类型：blend(均衡)/growth(成长)/value(价值)/dividend(红利)/unknown(未分类)
        "style_type": "string",

        # ===== 维度标签 → 风格大类（华泰2020五大风格+微调）=====
        # 五大风格板块：周期/消费/金融/成长/稳定；港股/跨境/other
        "sector_group": "string",

        # ===== 流动性分档（全市场 ETF 每日截面排名）=====
        # 日成交额在全市场 ETF 中的分位（0~1，0=流动性最差，1=流动性最好）
        "amount_rank": "float",
        # 基金规模在全市场 ETF 中的分位（0~1，0=最小规模，1=最大规模）
        "size_rank": "float",
    }

    # ============================================================
    # get_data：取基金日行情基础表
    # ============================================================
    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """取 fund_daily 基础数据（按 trade_date 区间）。"""
        s = self._ymd_to_dashed(start_date) if start_date else None
        e = self._ymd_to_dashed(end_date) if end_date else None

        query = "SELECT * FROM fund_daily WHERE 1=1"
        if s:
            query += f" AND trade_date >= '{s}'"
        if e:
            query += f" AND trade_date <= '{e}'"
        query += " ORDER BY ts_code, trade_date"
        logger.info(f"取 fund_daily: {s or '开始'} ~ {e or '结束'}")
        return pd.read_sql(query, self.engine)

    # ============================================================
    # process_data：join → 维度标签 → 流动性分档
    # ============================================================
    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """加工为宽表（join 复权/净值/份额/基本信息 + 维度标签 + 截面排名）。

        辅助表（fund_adj/nav/share/basic）从 MySQL 按需读取，
        读取区间由 params 中的 start_date/end_date 决定。
        """
        import time

        if data.empty:
            logger.warning("fund_daily 输入数据为空，返回空 DataFrame")
            return data

        start_date = params.get("start_date")
        end_date = params.get("end_date")
        start_time = time.time()
        logger.info(f"开始加工 ETF 日线宽表，fund_daily 输入 {len(data)} 条")

        # Step 0: 基础 daily
        result = data.copy()

        # Step 1: join adj_factor（行情复权）
        result = self._join_fund_adj(result, start_date, end_date)
        t1 = time.time()
        logger.info(f"[1/7] join fund_adj 完成，+{t1 - start_time:.1f}s")

        # Step 2: 计算派生行情列（log_return / vwap）
        result = self._derive_market_cols(result)
        t2 = time.time()
        logger.info(f"[2/7] 派生行情列 完成，+{t2 - t1:.1f}s")

        # Step 3: merge_asof fund_nav（PIT 不穿越）
        result = self._join_fund_nav(result, start_date, end_date)
        t3 = time.time()
        logger.info(f"[3/7] join fund_nav (asof) 完成，+{t3 - t2:.1f}s")

        # Step 4: merge_asof fund_share（PIT 不穿越）
        result = self._join_fund_share(result, start_date, end_date)
        t4 = time.time()
        logger.info(f"[4/7] join fund_share (asof) 完成，+{t4 - t3:.1f}s")

        # Step 5: join fund_basic（静态属性） + 维度标签
        result = self._join_fund_basic_and_classify(result)
        t5 = time.time()
        logger.info(f"[5/7] join fund_basic + 维度标签 完成，+{t5 - t4:.1f}s")

        # Step 6: 派生指标（discount_rate / fund_size / list_days / is_suspend）
        result = self._derive_fund_cols(result)
        t6 = time.time()
        logger.info(f"[6/7] 派生 fund 指标 完成，+{t6 - t5:.1f}s")

        # Step 7: 流动性 + 规模分档（每日截面排名）
        result = self._add_cross_sectional_ranks(result)
        t7 = time.time()
        logger.info(f"[7/7] 截面排名 完成，+{t7 - t6:.1f}s")

        # 收尾
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        # trade_date 转回字符串（加工层统一 yyyy-mm-dd）
        if pd.api.types.is_datetime64_any_dtype(result["trade_date"]):
            result["trade_date"] = result["trade_date"].dt.strftime("%Y-%m-%d")
        result = result[self.output_schema.keys()]  # 仅保留声明列
        total = time.time() - start_time
        logger.info(f"ETF 日线宽表完成: {len(result)} 条, 总耗时 {total:.1f}s")
        return result

    # ============================================================
    # 内部 join 方法
    # ============================================================

    def _join_fund_adj(
        self, df: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]
    ) -> pd.DataFrame:
        """left join fund_adj 复权因子（ts_code + trade_date）。"""
        s = self._ymd_to_dashed(start_date)
        e = self._ymd_to_dashed(end_date)
        q = "SELECT ts_code, trade_date, adj_factor FROM fund_adj WHERE 1=1"
        if s:
            q += f" AND trade_date >= '{s}'"
        if e:
            q += f" AND trade_date <= '{e}'"
        adj = pd.read_sql(q, self.engine)
        if adj.empty:
            df["adj_factor"] = 1.0
            return df
        return df.merge(
            adj, on=["ts_code", "trade_date"], how="left",
        )

    def _derive_market_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算派生行情列：log_return 对数收益率、vwap 日内均价。"""
        # 对数收益率：ln(1 + pct_chg/100)
        df["log_return"] = np.log(1 + df["pct_chg"].fillna(0) / 100)
        # 均价 = 成交额(千元) × 1000 / (成交量(手) × 100)
        # vol 单位手(100份), amount 单位千元
        denom = df["vol"] * 100
        df["vwap"] = np.where(
            denom > 1,
            df["amount"].fillna(0) * 1000 / denom,
            df["close"].fillna(0),
        )
        return df

    def _join_fund_nav(
        self, df: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]
    ) -> pd.DataFrame:
        """merge_asof backward 净值（nav_date ≤ trade_date，PIT 不穿越）。

        fund_nav 不是每日更新（通常每周 3-5 次），用 asof backward 取最近的已公布净值。
        前溯 60 天保证 asof 不落空。
        """
        s = self._ymd_to_dashed(start_date)
        e = self._ymd_to_dashed(end_date)
        nav_s = self._days_ago(s, 60) if s else None

        q = """SELECT ts_code, nav_date, unit_nav, accum_nav, adj_nav
               FROM fund_nav WHERE 1=1"""
        if nav_s:
            q += f" AND nav_date >= '{nav_s}'"
        if e:
            q += f" AND nav_date <= '{e}'"
        q += " ORDER BY ts_code, nav_date"
        nav = pd.read_sql(q, self.engine)

        if nav.empty:
            for col in ("unit_nav", "accum_nav", "adj_nav"):
                df[col] = None
            return df

        # merge_asof: per ts_code, backward asof nav_date ≤ trade_date
        # 注意：merge_asof with by 时，只按 on 列排序（不按 by 列），
        # merge_asof 内部处理 by 分组；trade_date 需转为 datetime
        nav = nav.rename(columns={"nav_date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        nav["trade_date"] = pd.to_datetime(nav["trade_date"])
        nav = nav.sort_values("trade_date").reset_index(drop=True)
        df = df.sort_values("trade_date").reset_index(drop=True)
        cols = ["unit_nav", "accum_nav", "adj_nav"]
        merged = pd.merge_asof(
            df, nav[["ts_code", "trade_date"] + cols],
            on="trade_date", by="ts_code",
            direction="backward",
            allow_exact_matches=True,
        )
        return merged

    def _join_fund_share(
        self, df: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]
    ) -> pd.DataFrame:
        """merge_asof backward 份额（PIT 不穿越）。

        fund_share 不是每日公布（月频居多），取最近公布值。前溯 60 天。
        """
        s = self._ymd_to_dashed(start_date)
        e = self._ymd_to_dashed(end_date)
        share_s = self._days_ago(s, 60) if s else None

        q = "SELECT ts_code, trade_date, fd_share FROM fund_share WHERE 1=1"
        if share_s:
            q += f" AND trade_date >= '{share_s}'"
        if e:
            q += f" AND trade_date <= '{e}'"
        q += " ORDER BY ts_code, trade_date"
        share = pd.read_sql(q, self.engine)

        if share.empty:
            df["fd_share"] = None
            return df

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        share["trade_date"] = pd.to_datetime(share["trade_date"])
        share = share.sort_values("trade_date").reset_index(drop=True)
        df = df.sort_values("trade_date").reset_index(drop=True)
        merged = pd.merge_asof(
            df, share[["ts_code", "trade_date", "fd_share"]],
            on="trade_date", by="ts_code",
            direction="backward",
            allow_exact_matches=True,
        )
        return merged

    def _join_fund_basic_and_classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """left join fund_basic（名称/类型/上市日） + 应用 classify_etf 维度标签。"""
        basic = pd.read_sql(
            "SELECT ts_code, name, fund_type, invest_type, list_date FROM fund_basic",
            self.engine,
        )

        # left join fund_basic
        df = df.merge(
            basic, on="ts_code", how="left", suffixes=("", "_basic"),
        )

        # 应用 classify_etf（从 ETF 名称提取指数 → 行业/风格/大类）
        idx_names = df["name"].apply(extract_index_name)
        classifications = idx_names.apply(lambda x: pd.Series(classify_etf(x or "")))
        classifications.columns = ["sw_l1_code", "sw_l1_name", "style_cap", "style_type", "sector_group"]
        df = pd.concat([df.reset_index(drop=True), classifications.reset_index(drop=True)], axis=1)
        return df

    def _derive_fund_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 ETF 派生指标。"""
        # 折溢价率 = close/unit_nav - 1（仅当 unit_nav > 0 且 close > 0）
        valid = (df["unit_nav"].fillna(0) > 0) & (df["close"].fillna(0) > 0)
        df["discount_rate"] = np.where(
            valid,
            df["close"] / df["unit_nav"] - 1,
            None,
        )

        # 基金规模(亿元) = close(元/份) × fd_share(万份) / 1e4
        # close 单位元/份，fd_share 单位万份 → 元 × 万份 = 万元 / 10000 = 亿元
        valid_size = (df["close"].fillna(0) > 0) & (df["fd_share"].fillna(0) > 0)
        df["fund_size"] = np.where(
            valid_size,
            df["close"] * df["fd_share"] / 1e4,
            None,
        )

        # 上市天数（calendar day count from list_date to trade_date）
        df["trade_date_dt"] = pd.to_datetime(df["trade_date"])
        df["list_date_dt"] = pd.to_datetime(df["list_date"], errors="coerce")
        delta = (df["trade_date_dt"] - df["list_date_dt"]).dt.days
        df["list_days"] = delta.fillna(-1).astype(int)  # list_date 缺失 → -1

        # 停牌标记（vol=0 且 pct_chg=0 → 停牌）
        df["is_suspend"] = (
            (df["vol"].fillna(-1) == 0) & (df["pct_chg"].fillna(-999) == 0)
        ).fillna(False).astype(int)

        # 清理临时列
        df.drop(columns=["trade_date_dt", "list_date_dt"], inplace=True, errors="ignore")
        # 清理可能从 basic join 带来的重复列
        df.drop(columns=[c for c in df.columns if c.endswith("_basic")], inplace=True, errors="ignore")
        return df

    def _add_cross_sectional_ranks(self, df: pd.DataFrame) -> pd.DataFrame:
        """每日截面分档：amount_rank（成交额排名）、size_rank（规模排名）。"""
        df["amount_rank"] = df.groupby("trade_date")["amount"].rank(pct=True)
        df["size_rank"] = df.groupby("trade_date")["fund_size"].rank(pct=True)
        return df

    # ============================================================
    # 日期工具
    # ============================================================
    @staticmethod
    def _ymd_to_dashed(date_str: Optional[str]) -> Optional[str]:
        """yyyymmdd → yyyy-mm-dd。"""
        if not date_str or len(date_str) != 8:
            return None
        try:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        except (TypeError, IndexError):
            return None

    @staticmethod
    def _days_ago(date_str: Optional[str], days: int) -> Optional[str]:
        """日期字符串前推 N 天（yyyy-mm-dd）。"""
        if not date_str:
            return None
        try:
            dt = pd.Timestamp(date_str) - pd.Timedelta(days=days)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None
