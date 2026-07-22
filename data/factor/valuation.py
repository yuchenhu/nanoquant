"""估值因子（从 financial_indicators_snapshot 按 snapshot_date PIT 读指标，叉乘衍生）。

表名：factor_valuation
主键：snapshot_date + ts_code
biz_date_col：snapshot_date（月末快照）
write_mode：upsert
依赖：panel_financial_indicators_snapshot

================================ 因子设计 ================================
1. 基础列（估值比值，来自 indicators 表，按 MV 分母）：
   bp, rep, sp_q, gpp_q, ep_q, ocfp_q, ebitp_q, divp_ttm
   及其 TTM: sp_ttm, gpp_ttm, ep_ttm, ocfp_ttm, ebitp_ttm
   手动派生 (费用比率):  arp_q  = admp_q + rdp_q
                       artp_q = admp_q + rdp_q + taxp_q

2. 衍生方法（对 past 36 月 snapshot ≈ 12 期季报做时序统计）：
   - mean, std, zscore, tsrank(rank pct) — 稳定性 + 相对位置
   - momentum = (latest - lag4) / |lag4| — 同比变化率（改善/恶化）
   - neg_cnt — 可负列（ep/gpp/ocfp/ebitp）的负值计数

3. 全量叉乘：每个基础列 × 适用的衍生方法 = ~90 列
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from data.factor.base import FactorCalculator

logger = logging.getLogger(__name__)


class ValuationCalculator(FactorCalculator):
    """估值因子（从 PIT 财务指标 12 月窗口 全量叉乘）。"""

    table_name = "valuation"  # → factor_valuation
    primary_keys = ["snapshot_date", "ts_code"]
    biz_date_col = "snapshot_date"
    write_mode = "overwrite"
    partition_col = "snapshot_date"

    # 从 indicators 表读哪些列（用于后续取数）
    INDICATOR_COLS = [
        "snapshot_date", "ts_code", "end_date", "actual_date",
        "bp", "rep",
        "sp_q", "gpp_q", "ep_q", "ocfp_q", "ebitp_q",
        "sp_ttm", "gpp_ttm", "ep_ttm", "ocfp_ttm", "ebitp_ttm",
        "divp_ttm",
        "admp_q", "rdp_q", "taxp_q",  # 用于派生 arp_q / artp_q
    ]
    # snapshot 回看窗口（月频 × 36 = 三年 ≈ 12 期季报，tsrank/momentum 有意义）
    LOOKBACK_MONTHS = 36
    # 最小有效 snapshot 期数（不足则跳过该 stock）
    MIN_PERIODS = 4

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._init_column_config()
        self.logger.info("ValuationCalculator 初始化")

    # ===== output_schema 动态生成 =====

    def _init_column_config(self) -> None:
        """定义基础列分组 + 衍生方法，动态构建 schema。"""

        # 非负列（不适合 neg_cnt）
        self.non_neg_base = [
            "bp", "rep",
            "sp_q", "sp_ttm",
            "divp_ttm",
        ]
        # 可负列（适合 neg_cnt）
        self.signed_base = [
            "gpp_q", "ep_q", "ocfp_q", "ebitp_q",
            "gpp_ttm", "ep_ttm", "ocfp_ttm", "ebitp_ttm",
        ]
        # 手动派生 (费用类，可负)
        self.derived_base = ["arp_q", "artp_q"]

        self.all_base = self.non_neg_base + self.signed_base + self.derived_base

        # 衍生方法
        self.methods_stability = ["mean", "std", "zscore", "tsrank"]
        self.methods_momentum = ["momentum"]
        self.methods_neg = ["neg_cnt"]

        # 构建 output_schema
        schema: Dict[str, str] = {
            "snapshot_date": "string",
            "ts_code": "string",
            "report_cnt": "int",
        }
        # raw (latest)
        for c in self.all_base:
            schema[c] = "float"
        # 非负列 × (stability + momentum)
        for c in self.non_neg_base:
            for m in self.methods_stability + self.methods_momentum:
                schema[f"{c}_{m}"] = "float"
        # 可负列 × (stability + momentum + neg_cnt)
        for c in self.signed_base:
            for m in self.methods_stability + self.methods_momentum:
                schema[f"{c}_{m}"] = "float"
            schema[f"{c}_neg_cnt"] = "int"
        # 派生列 × (stability + momentum)
        for c in self.derived_base:
            for m in self.methods_stability + self.methods_momentum:
                schema[f"{c}_{m}"] = "float"
        self.output_schema = schema

    # ===== get_data =====

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """从 panel_financial_indicators_snapshot 读 PIT 指标。

        start_date/end_date 是 snapshot_date 区间。
        向前回看 LOOKBACK_MONTHS 个月以保证有足够历史期数。
        """
        if not start_date or not end_date:
            return pd.DataFrame()
        # 构造 SQL 日期串（yyyy-mm-dd）
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        # 回看下界
        from datetime import datetime
        from dateutil.relativedelta import relativedelta
        sd_dt = datetime.strptime(start_date, "%Y%m%d")
        read_start = (sd_dt - relativedelta(months=self.LOOKBACK_MONTHS)).strftime("%Y-%m-%d")

        cols_str = ", ".join(self.INDICATOR_COLS)
        query = f"""
            SELECT {cols_str}
            FROM panel_financial_indicators_snapshot
            WHERE snapshot_date >= '{read_start}' AND snapshot_date <= '{ed}'
            ORDER BY ts_code, snapshot_date, end_date
        """
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query = query.rstrip(" ORDER BY ts_code, snapshot_date, end_date")
            query += f" AND ts_code IN ({codes_str}) ORDER BY ts_code, snapshot_date, end_date"
        self.logger.info(f"取 indicators: snapshot {read_start} ~ {ed}")
        return pd.read_sql(query, self.engine)

    # ===== process_data =====

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return data
        start_date = params.get("start_date")
        end_date = params.get("end_date")

        df = data.copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df["end_date"] = pd.to_datetime(df["end_date"])

        # 1. 每个 (snapshot_date, ts_code) 取最新 end_date（同一 snapshot 可能有多版修订）
        df = df.sort_values(["ts_code", "snapshot_date", "end_date"])
        df = df.groupby(["ts_code", "snapshot_date"], as_index=False).tail(1)

        # 2. 手动派生费用比率
        df["arp_q"] = df["admp_q"].fillna(0) + df["rdp_q"].fillna(0)
        df["artp_q"] = df["admp_q"].fillna(0) + df["rdp_q"].fillna(0) + df["taxp_q"].fillna(0)

        # 3. 确定输出窗口 vs 历史窗口
        #    hist_df = 全量（含 lookback 回看），用于每个 snapshot 取前 12 期
        #    output_snaps = 输出窗口 [start_date, end_date] 内的 snapshot_date
        hist_df = df.copy()
        if start_date and end_date:
            sd_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
            ed_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
            output_snaps = hist_df[
                (hist_df["snapshot_date"] >= sd_str) & (hist_df["snapshot_date"] <= ed_str)
            ]
        else:
            output_snaps = hist_df

        if output_snaps.empty:
            return pd.DataFrame()

        # 4. 每组取最近 12 期 snapshot, group 后聚合
        results = []
        for (ts_code, snap_dt), _ in output_snaps.groupby(["ts_code", "snapshot_date"]):
            # 从 hist_df（含 lookback）取该 stock 在 snap_dt 之前的最近 12 期
            stock_hist = hist_df[
                (hist_df["ts_code"] == ts_code) & (hist_df["snapshot_date"] <= snap_dt)
            ]
            last12 = stock_hist.sort_values("snapshot_date").tail(self.LOOKBACK_MONTHS)
            if len(last12) < self.MIN_PERIODS:
                continue
            row = self._compute_row(ts_code, snap_dt, last12)
            results.append(row)

        if not results:
            return pd.DataFrame()
        result = pd.DataFrame(results)
        # 按 output_schema 重排列（额外列忽略）
        cols = [c for c in self.output_schema if c in result.columns]
        result = result[cols]
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        self.logger.info(f"估值因子完成: {len(result)} 条")
        return result

    # ===== 单行计算 =====

    def _compute_row(self, ts_code: str, snap_dt: pd.Timestamp, hist: pd.DataFrame) -> dict:
        row: dict = {
            "snapshot_date": snap_dt.strftime("%Y-%m-%d"),
            "ts_code": ts_code,
            "report_cnt": len(hist),
        }

        # raw (latest)
        latest = hist.iloc[-1]
        for c in self.all_base:
            val = latest.get(c)
            row[c] = float(val) if pd.notna(val) else None

        # 非负列衍生
        for c in self.non_neg_base:
            series = hist[c].dropna()
            self._add_derivations(row, c, series, include_neg=False)

        # 可负列衍生
        for c in self.signed_base:
            series = hist[c].dropna()
            self._add_derivations(row, c, series, include_neg=True)

        # 派生列衍生
        for c in self.derived_base:
            series = hist[c].dropna()
            self._add_derivations(row, c, series, include_neg=False)

        return row

    def _add_derivations(self, row: dict, col: str, series: pd.Series, *, include_neg: bool) -> None:
        """对单列 series 计算稳定性/动量/负计数，写入 row。"""
        if len(series) < self.MIN_PERIODS:
            return

        vals = series.values.astype(float)
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals, ddof=0))

        row[f"{col}_mean"] = mean_v
        row[f"{col}_std"] = std_v

        # zscore: (latest - mean) / std
        latest_v = vals[-1]
        row[f"{col}_zscore"] = float((latest_v - mean_v) / std_v) if std_v > 0 else None

        # tsrank: rank pct (排除自身)
        n = len(vals)
        rank = float(np.searchsorted(np.sort(vals[:-1]), latest_v, side="right"))
        row[f"{col}_tsrank"] = rank / (n - 1) if n > 1 else None

        # momentum: (latest - lag12) / |lag12|（12 期前 ≈ 4 份季报，同比）
        MOM_LAG = 12
        if len(vals) > MOM_LAG:
            lag_v = vals[-(MOM_LAG + 1)]
            if abs(lag_v) > 1e-12:
                row[f"{col}_momentum"] = float((latest_v - lag_v) / abs(lag_v))

        # neg_cnt
        if include_neg:
            row[f"{col}_neg_cnt"] = int((vals < 0).sum())
