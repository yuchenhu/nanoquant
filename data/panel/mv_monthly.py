"""个股×月 市值快照 Panel（从 data/sql/mv_monthly.py 迁移）。

表名：panel_mv_monthly
主键：ts_code + trade_date（月末最后交易日）
biz_date_col：trade_date
依赖：stock_daily_basic
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.dates import get_trade_dates_between
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class MvMonthlyCalculator(PanelCalculator):
    """个股×月 市值快照（每月最后交易日的 total_mv/circ_mv/total_share/float_share）。

    月频固化：update 只取区间内【每月最后交易日】，逐月落库+推水位。
    若不固化，增量模式会把"月中部分天"（如 7/15）当月末行写入，且与真正的
    月末行（7/31）在 PK (ts_code, trade_date) 下并存 -> 同月多行脏数据。
    """

    table_name = "mv_monthly"  # → panel_mv_monthly
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "total_mv": "float", "circ_mv": "float",
        "total_share": "float", "float_share": "float",
    }

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("MvMonthlyCalculator 初始化")

    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """月频快照：只写区间内每月最后交易日。

        性能要点（2026-10-04 修订）：读取按【年】分块单次扫描（每块一次范围查询，
        ROW_NUMBER 在 SQL 内完成月内去重），再在 pandas 侧只挑月末行落库。
        绝不能逐月单查——stock_daily_basic 1400 万行且 trade_date 非索引前导列，
        逐月查 = 每月一次全表扫 × 198 个月。
        """
        from core.dates import get_today_str

        start_date = self._normalize_date(start_date) or self._next_after_biz_date() or get_today_str()
        end_date = self._normalize_date(end_date) or get_today_str()

        month_ends = self._month_end_dates(start_date, end_date)
        self.logger.info(
            f"{self.table_name} update: 月频快照 [{start_date}, {end_date}] -> {len(month_ends)} 个月末"
        )
        if not month_ends:
            return pd.DataFrame()

        # 按年分块读取（块内单次扫描），月末集合透传给 process_data 过滤
        all_results: List[pd.DataFrame] = []
        for lo, hi in self._year_chunks(month_ends):
            try:
                raw = self.get_data(lo, hi, **params)
                result = self.process_data(raw, month_ends=month_ends, **params)
                if result is not None and not result.empty:
                    self.save_to_database(result)
                    all_results.append(result)
                    self.logger.info(f"{self.table_name} [{lo}, {hi}] 写入 {len(result)} 行（月末快照）")
            except Exception as e:
                self.logger.error(f"{self.table_name} [{lo}, {hi}] 失败: {e}")
        if not all_results:
            return pd.DataFrame()
        max_biz = self._max_biz_date(pd.concat(all_results, ignore_index=True))
        if max_biz:
            self._set_biz_date(max_biz, sum(len(x) for x in all_results))
        return pd.concat(all_results, ignore_index=True)

    @staticmethod
    def _year_chunks(month_ends: List[str]) -> List[tuple]:
        """把月末日期序列按年份归块，返回 [(年首或首月末, 年末或末月末)] 读取区间。"""
        by_year: Dict[str, List[str]] = {}
        for m in month_ends:
            by_year.setdefault(m[:4], []).append(m)
        return [(ms[0], ms[-1]) for _, ms in sorted(by_year.items())]

    @staticmethod
    def _month_end_dates(start_date: str, end_date: str) -> List[str]:
        """[start_date, end_date] 内每月最后一个交易日（yyyymmdd，升序）。"""
        days = get_trade_dates_between(start_date, end_date)
        by_month: Dict[str, str] = {}
        for d in days:  # 升序遍历，同月后值覆盖 -> 月最后交易日
            by_month[d[:6]] = d
        return [by_month[m] for m in sorted(by_month)]

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        query = """
        SELECT ts_code, trade_date, total_mv, circ_mv, total_share, float_share,
        ROW_NUMBER() OVER (PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m') ORDER BY trade_date DESC) as rn
        FROM stock_daily_basic WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"
        query += " ORDER BY ts_code, trade_date"
        return pd.read_sql(query, self.engine)

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return data
        result = data[data.rn == 1].drop('rn', axis=1)
        # 只保留月末交易日行（月频固化；month_ends 由 update 透传）
        month_ends = set(params.get("month_ends") or [])
        if month_ends:
            key = result["trade_date"].astype(str).str.replace("-", "")
            result = result[key.isin(month_ends)]
        # NaN -> NULL 清洗：replace(nan->None) 会被 pandas coerce 回 NaN，不可靠；
        # 稳态写法 = 先转 object dtype 再 where 填 None（同 financial_statements_snapshot 修复）
        num_cols = result.select_dtypes(include=[np.number]).columns
        result[num_cols] = result[num_cols].replace([np.inf, -np.inf], np.nan)
        return result.astype(object).where(pd.notnull(result), None)
