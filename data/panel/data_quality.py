"""数据质量监控 Panel：每个数据源 x 每个"应有业务日期" -> 实际行数。

表名：panel_data_quality
主键：source_table + biz_date
每行 = 某接入层表在某个"应该有数的日期"的实际行数（row_count=0 即整天缺失）。

做法（与 scripts/data_dqc.py 同源）：逐表用固定日期基准（交易日 / 周末 / 月末 /
季度末）做左表，LEFT JOIN 实际数据按日期 count，落成一张可 SQL 查询的明细表：
  - status=MISSING : 基准里有、实际 row_count=0（整天没拉到）
  - status=PARTIAL : row_count 远低于当年中位数（请求超时只拉了一半）
  - status=OK      : 正常

监控表特性：每次全量重算 + truncate 重建全表，不走增量水位
（year_median 需要"当年全部覆盖日期"才算得准，增量小区间会失真）。

查询示例：
  SELECT source_table, biz_date FROM panel_data_quality
  WHERE status='MISSING' ORDER BY source_table, biz_date;

  SELECT source_table, LEFT(biz_date,4) y, SUM(status='MISSING') miss,
         SUM(status='PARTIAL') part
  FROM panel_data_quality GROUP BY source_table, y;
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from data.panel.base import PanelCalculator

# ============================ 表配置 ============================
# (table, date_col, grain, start, check_partial)
# grain        : trade_day / week_end / month_end / quarter_end
# start        : 该源数据真正开始有数的日期（早年为空是数据本身没有，不算缺数）
# check_partial: 是否检查"行数远低于当年中位数"（行情主表开，行数天然波动的表关）
DATE_TABLES = [
    ("stock_daily",        "trade_date", "trade_day",   "2010-01-01", True),
    ("stock_daily_basic",  "trade_date", "trade_day",   "2010-01-01", True),
    ("adj_factor",         "trade_date", "trade_day",   "2010-01-01", True),
    ("moneyflow",          "trade_date", "trade_day",   "2010-01-01", True),
    ("index_daily",        "trade_date", "trade_day",   "2010-01-01", True),
    ("index_daily_basic",  "trade_date", "trade_day",   "2010-01-01", True),
    ("fund_daily",         "trade_date", "trade_day",   "2010-01-01", True),
    ("fund_adj",           "trade_date", "trade_day",   "2010-01-01", True),
    ("sw_daily",           "trade_date", "trade_day",   "2010-01-01", True),
    ("stock_st",           "trade_date", "trade_day",   "2010-01-01", False),
    ("suspend",            "trade_date", "trade_day",   "2010-01-01", False),
    ("fund_share",         "trade_date", "trade_day",   "2010-01-01", False),
    ("margin",             "trade_date", "trade_day",   "2010-03-31", False),
    ("moneyflow_hsgt",     "trade_date", "trade_day",   "2014-11-17", False),
    ("limit_list_d",       "trade_date", "trade_day",   "2020-01-01", False),
    ("stock_weekly",       "trade_date", "week_end",    "2010-01-01", False),
    ("stock_monthly",      "trade_date", "month_end",   "2010-01-01", False),
    ("index_weight",       "trade_date", "month_end",   "2010-01-01", False),
    ("income",             "end_date",   "quarter_end", "2010-03-31", False),
    ("balancesheet",       "end_date",   "quarter_end", "2010-03-31", False),
    ("cashflow",           "end_date",   "quarter_end", "2010-03-31", False),
    ("disclosure_date",    "end_date",   "quarter_end", "2010-03-31", False),
]

PARTIAL_RATIO = 0.5  # row_count < 当年中位数 * 该比例 -> PARTIAL


def _d2s(v: Any) -> Optional[str]:
    """把 DB 取出的日期值统一成 'yyyy-mm-dd' 字符串。"""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    if len(s) == 8 and s.isdigit():  # yyyymmdd
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


class DataQualityCalculator(PanelCalculator):
    """数据质量监控：每数据源 x 每应有日期的实际行数（全量重算，truncate 重建）。"""

    table_name = "data_quality"  # -> panel_data_quality
    primary_keys = ["source_table", "biz_date"]
    biz_date_col = "biz_date"
    write_mode = "truncate"
    output_schema = {
        "source_table": "string",
        "biz_date": "string",      # yyyymmdd（用字符串便于按年切片，无需与接入层 join）
        "group_col": "string",     # 该源表按哪个列做基准（trade_date/end_date）。注意不可叫 date_col：列名含 date_ 会被 schema 强制建成 DATE
        "grain": "string",
        "row_count": "int",
        "year_median": "int",      # 当年覆盖日期 row_count 的中位数（PARTIAL 判定基准）
        "status": "string",        # OK / MISSING / PARTIAL
    }

    # ---------- 覆写 update：监控表全量重算，不走水位增量 ----------
    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        start_date = self._normalize_date(start_date) or None
        end_date = self._normalize_date(end_date) or None
        self.logger.info(
            "panel_data_quality 全量重算: 下界=%s 上界=%s",
            start_date or "各表自身 start", end_date or "昨天",
        )
        raw = self.get_data(start_date, end_date, **params)
        if raw is None or raw.empty:
            self.logger.warning("panel_data_quality get_data 返回空，跳过")
            return pd.DataFrame()
        result = self.process_data(raw, start_date=start_date, end_date=end_date, **params)
        if result is None or result.empty:
            self.logger.warning("panel_data_quality process_data 返回空，跳过")
            return pd.DataFrame()
        self.save_to_database(result)  # truncate 全量刷新，不写水位
        self.logger.info("panel_data_quality 落库 %d 行", len(result))
        return result

    # ---------- 取数：逐表生成基准日期 + 实际行数 ----------
    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        all_td = self._all_trade_days()
        if not all_td:
            self.logger.error("trade_cal 为空，无法生成日期基准")
            return pd.DataFrame()

        if end_date:
            cutoff = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        else:
            # 上界用"昨天"：今天的数据当晚才出，含今天会让每表恒报今天 MISSING
            cutoff = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

        rows: List[Dict[str, Any]] = []
        for table, date_col, grain, start, check_partial in DATE_TABLES:
            if not self._table_exists(table):
                self.logger.warning("[%s] 表不存在，跳过", table)
                continue
            base = self._base_dates(grain, start, cutoff, all_td)
            if not base:
                continue
            counts = self._date_counts(table, date_col)
            for d in base:
                rows.append({
                    "source_table": table,
                    "group_col": date_col,
                    "grain": grain,
                    "biz_date": d,  # 'yyyy-mm-dd'，process 里转 yyyymmdd
                    "row_count": int(counts.get(d, 0)),
                })
        return pd.DataFrame(rows)

    # ---------- 加工：算当年中位数 + status，按下界过滤 ----------
    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return pd.DataFrame()
        start_date = params.get("start_date")
        df = data.copy()
        df["year"] = df["biz_date"].str[:4]

        # 当年中位数（只用 row_count>0 的覆盖日期）
        pos = df[df["row_count"] > 0]
        med = (
            pos.groupby(["source_table", "year"], as_index=False)["row_count"]
            .median()
            .rename(columns={"row_count": "year_median"})
        )
        med["year_median"] = med["year_median"].round().astype(int)
        df = df.merge(med, on=["source_table", "year"], how="left")
        df["year_median"] = df["year_median"].fillna(0).astype(int)

        # status
        cp = df["source_table"].map(
            {t: c for t, _, _, _, c in DATE_TABLES}
        ).fillna(False)
        is_partial = (
            cp
            & (df["year_median"] > 0)
            & (df["row_count"] < df["year_median"] * PARTIAL_RATIO)
        )
        df["status"] = np.where(
            df["row_count"] == 0,
            "MISSING",
            np.where(is_partial, "PARTIAL", "OK"),
        )

        # 'yyyy-mm-dd' -> 'yyyymmdd'
        df["biz_date"] = df["biz_date"].str.replace("-", "", regex=False)

        if start_date:
            df = df[df["biz_date"] >= start_date]

        cols = ["source_table", "biz_date", "group_col", "grain",
                "row_count", "year_median", "status"]
        return df[cols].reset_index(drop=True)

    # ============================ 日期基准 ============================
    def _all_trade_days(self) -> List[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT cal_date FROM trade_cal WHERE is_open=1 ORDER BY cal_date")
            ).fetchall()
        return [_d2s(r[0]) for r in rows if r[0] is not None]

    def _base_dates(self, grain: str, start: str, cutoff: str, all_td: List[str]) -> List[str]:
        if grain == "trade_day":
            return [d for d in all_td if start <= d <= cutoff]
        if grain == "month_end":
            last: Dict[str, str] = {}
            for d in all_td:
                if start <= d <= cutoff:
                    last[d[:7]] = d
            return sorted(last.values())
        if grain == "week_end":
            last: Dict[Any, str] = {}
            for d in all_td:
                if start <= d <= cutoff:
                    iso = datetime.strptime(d, "%Y-%m-%d").isocalendar()
                    last[(iso[0], iso[1])] = d
            return sorted(last.values())
        if grain == "quarter_end":
            out: List[str] = []
            y0, y1 = int(start[:4]), int(cutoff[:4])
            for y in range(y0, y1 + 1):
                for mmdd in ("03-31", "06-30", "09-30", "12-31"):
                    qd = f"{y}-{mmdd}"
                    if start <= qd <= cutoff:
                        out.append(qd)
            return out
        self.logger.warning("未知 grain: %s", grain)
        return []

    # ============================ 实际行数 ============================
    def _table_exists(self, table: str) -> bool:
        with self.engine.connect() as conn:
            r = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=:t"
                ),
                {"t": table},
            ).fetchone()
        return bool(r and r[0] > 0)

    def _date_counts(self, table: str, date_col: str) -> Dict[str, int]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT `{date_col}`, COUNT(*) FROM `{table}` GROUP BY `{date_col}`")
            ).fetchall()
        return {_d2s(r[0]): int(r[1]) for r in rows if r[0] is not None}
