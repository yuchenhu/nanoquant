"""交易日工具（从 data/utils/date_utils.py 提升）。

改进：
- 交易日历改为懒加载（_get_trade_cal()），避免 import 时副作用
- 日期统一 yyyymmdd 字符串
- print → logger

兼容：data/utils/date_utils.py 已改为 shim 重导出本模块。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from config.database import engine

logger = logging.getLogger(__name__)

# ==================== 交易日历缓存（懒加载） ====================

_TRADE_CAL_DF: Optional[pd.DataFrame] = None


def _get_trade_cal() -> pd.DataFrame:
    """懒加载交易日历（首次调用时从 trade_cal 表读，之后用缓存）。"""
    global _TRADE_CAL_DF
    if _TRADE_CAL_DF is None:
        _TRADE_CAL_DF = pd.read_sql(
            "SELECT cal_date, is_open FROM trade_cal ORDER BY cal_date", engine
        )
        _TRADE_CAL_DF["cal_date"] = (
            _TRADE_CAL_DF["cal_date"].astype(str).apply(lambda x: x.replace("-", ""))
        )
        logger.info(f"交易日历缓存已加载，共 {len(_TRADE_CAL_DF)} 条记录")
    return _TRADE_CAL_DF


def reload_trade_cal() -> None:
    """强制重新加载交易日历（trade_cal 表更新后调用）。"""
    global _TRADE_CAL_DF
    _TRADE_CAL_DF = None
    _get_trade_cal()


# ==================== 基础日期 ====================

def get_today_str(format_str: str = "%Y%m%d") -> str:
    """获取今天日期字符串。"""
    return datetime.now().strftime(format_str)


def get_recent_quarter_dates(date_str: Optional[str] = None, n: int = 4) -> List[str]:
    """获取指定日期前几个季度的季度末日期（yyyymmdd，从早到晚）。"""
    calc_date = datetime.now() if date_str is None else datetime.strptime(date_str, "%Y%m%d")
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

    current_quarter = (calc_date.month - 1) // 3 + 1
    current_year = calc_date.year

    q_month, q_day = quarter_ends[current_quarter]
    start_quarter = (
        current_quarter
        if datetime(current_year, q_month, q_day) <= calc_date
        else current_quarter - 1
    )
    start_year = current_year
    if start_quarter == 0:
        start_quarter = 4
        start_year -= 1

    quarter_dates: List[str] = []
    quarter, year = start_quarter, start_year
    for _ in range(n):
        month, day = quarter_ends[quarter]
        quarter_end = datetime(year, month, day)
        if quarter_end <= calc_date:
            quarter_dates.append(quarter_end.strftime("%Y%m%d"))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1

    return sorted(quarter_dates)


def get_month_start_end(date_str: Optional[str] = None) -> Dict[str, str]:
    """获取指定日期所在月份的第一天和最后一天（自然日）。"""
    if date_str:
        if "-" in date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            target_date = datetime.strptime(date_str, "%Y%m%d")
    else:
        target_date = datetime.now()

    month_start = datetime(target_date.year, target_date.month, 1)
    if target_date.month == 12:
        next_month = datetime(target_date.year + 1, 1, 1)
    else:
        next_month = datetime(target_date.year, target_date.month + 1, 1)
    month_end = next_month - timedelta(days=1)

    return {
        "start_date": month_start.strftime("%Y%m%d"),
        "end_date": month_end.strftime("%Y%m%d"),
    }


# ==================== 交易日判断 ====================

def is_trading_day(date_str: Optional[str] = None) -> bool:
    """判断是否为交易日。"""
    if date_str is None:
        date_str = get_today_str()

    cal = _get_trade_cal()
    date_row = cal[cal["cal_date"] == date_str]
    if not date_row.empty:
        return date_row["is_open"].iloc[0] == 1
    # 不在交易日历里，按周末兜底
    date_obj = datetime.strptime(date_str, "%Y%m%d")
    return date_obj.weekday() not in [5, 6]


def get_trade_dates_between(start_date: str, end_date: str) -> List[str]:
    """获取 [start_date, end_date] 闭区间内所有交易日（YYYYMMDD 字符串，升序）。

    依赖 trade_cal 表。若区间内无交易日返回空列表。
    """
    if not start_date or not end_date:
        return []
    if start_date > end_date:
        return []

    cal = _get_trade_cal()
    mask = (cal["cal_date"] >= start_date) & (cal["cal_date"] <= end_date) & (cal["is_open"] == 1)
    return cal.loc[mask, "cal_date"].tolist()


def find_nearest_trading_day(
    date_str: Optional[str] = None, backward: bool = True
) -> Optional[str]:
    """查找最近的交易日。

    backward=True 向前找（更早的交易日），False 向后找。
    """
    if date_str is None:
        date_str = get_today_str()

    cal = _get_trade_cal()
    trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)

    if date_str < min_trade_date:
        logger.warning(
            f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日"
        )
        return min_trade_date
    if date_str > max_trade_date:
        logger.warning(
            f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日"
        )
        return max_trade_date

    if backward:
        search_dates = cal[(cal["cal_date"] <= date_str) & (cal["is_open"] == 1)]
        search_dates = search_dates.sort_values("cal_date", ascending=False)
    else:
        search_dates = cal[(cal["cal_date"] >= date_str) & (cal["is_open"] == 1)]
        search_dates = search_dates.sort_values("cal_date")

    if not search_dates.empty:
        return search_dates.iloc[0]["cal_date"]
    return None


def get_previous_n_trading_date(
    date_str: Optional[str] = None, n_days: int = 1
) -> Optional[str]:
    """计算输入日期往前推 N 个交易日的日期。"""
    if date_str is None:
        date_str = get_today_str()

    cal = _get_trade_cal()
    trade_cal = cal[cal["is_open"] == 1].sort_values("cal_date").reset_index(drop=True)

    min_trade_date = trade_cal["cal_date"].min()
    max_trade_date = trade_cal["cal_date"].max()

    if date_str < min_trade_date:
        logger.warning(f"输入日期 {date_str} 小于最小交易日，返回最小交易日")
        return min_trade_date
    if date_str > max_trade_date:
        logger.warning(f"输入日期 {date_str} 大于最大交易日，返回最大交易日")
        return max_trade_date

    date_mask = trade_cal["cal_date"] <= date_str
    dates_before_input = trade_cal[date_mask].sort_values("cal_date", ascending=False)

    if dates_before_input.empty:
        logger.warning(f"输入日期 {date_str} 之前没有交易日")
        return None

    if date_str == dates_before_input.iloc[0]["cal_date"]:
        target_idx = n_days
    else:
        target_idx = n_days - 1

    if target_idx < len(dates_before_input):
        return dates_before_input.iloc[target_idx]["cal_date"]
    logger.warning(f"无法往前推 {n_days} 个交易日，最早只能到 {min_trade_date}")
    return min_trade_date


def get_next_n_trading_date(
    date_str: Optional[str] = None, n_days: int = 1
) -> Optional[str]:
    """计算输入日期往后推 N 个交易日的日期。"""
    if date_str is None:
        date_str = get_today_str()

    cal = _get_trade_cal()
    trade_cal = cal[cal["is_open"] == 1].sort_values("cal_date").reset_index(drop=True)

    min_trade_date = trade_cal["cal_date"].min()
    max_trade_date = trade_cal["cal_date"].max()

    if date_str < min_trade_date:
        return min_trade_date
    if date_str > max_trade_date:
        return max_trade_date

    date_mask = trade_cal["cal_date"] >= date_str
    dates_from_input = trade_cal[date_mask]

    if dates_from_input.empty:
        return max_trade_date

    if date_str == dates_from_input.iloc[0]["cal_date"]:
        target_idx = n_days
    else:
        target_idx = n_days - 1

    if target_idx < len(dates_from_input):
        return dates_from_input.iloc[target_idx]["cal_date"]
    return max_trade_date


# ==================== 周/月末交易日 ====================

def get_recent_weekday(date_str: Optional[str] = None) -> str:
    """获取最近一个周的最后交易日（<= 输入日期）。"""
    if date_str is None:
        date_str = get_today_str()
    input_date = datetime.strptime(date_str, "%Y%m%d")

    cal = _get_trade_cal()
    trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
    trade_dates_set = set(trade_dates)
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)

    if date_str < min_trade_date:
        return min_trade_date
    if date_str > max_trade_date:
        return max_trade_date

    original_input_str = date_str
    for _ in range(52):
        year, week_num, weekday = input_date.isocalendar()
        monday = input_date - timedelta(days=weekday - 1)
        week_dates = [
            (monday + timedelta(days=i)).strftime("%Y%m%d") for i in range(7)
        ]
        week_trading_days = [d for d in week_dates if d in trade_dates_set]
        if week_trading_days:
            last_trade_day = max(week_trading_days)
            if last_trade_day <= original_input_str:
                return last_trade_day
        input_date = input_date - timedelta(days=7)

    logger.warning(f"循环 52 周未找到符合条件的交易日，返回最大交易日期: {max_trade_date}")
    return max_trade_date


def get_recent_month(date_str: Optional[str] = None) -> str:
    """获取最近一个月的最后交易日（<= 输入日期）。"""
    if date_str is None:
        date_str = get_today_str()
    input_date = datetime.strptime(date_str, "%Y%m%d")

    cal = _get_trade_cal()
    trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
    trade_dates_set = set(trade_dates)
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)

    if date_str < min_trade_date:
        return min_trade_date
    if date_str > max_trade_date:
        return max_trade_date

    current_date = input_date
    for _ in range(12):
        year = current_date.year
        month_num = current_date.month
        if month_num == 12:
            next_month_first_day = datetime(year + 1, 1, 1)
        else:
            next_month_first_day = datetime(year, month_num + 1, 1)
        month_first_day = datetime(year, month_num, 1)
        month_last_day = next_month_first_day - timedelta(days=1)

        month_dates: List[str] = []
        current_day = month_first_day
        while current_day <= month_last_day:
            month_dates.append(current_day.strftime("%Y%m%d"))
            current_day += timedelta(days=1)

        month_trading_days = [d for d in month_dates if d in trade_dates_set]
        if month_trading_days:
            last_trade_day = max(month_trading_days)
            if last_trade_day <= date_str:
                return last_trade_day
        # 月份 -1
        if month_num == 1:
            current_date = datetime(year - 1, 12, 1)
        else:
            current_date = datetime(year, month_num - 1, 1)

    logger.warning(f"循环 12 个月未找到符合条件的交易日，返回最大交易日期: {max_trade_date}")
    return max_trade_date


def get_monthly_last_tradedate(engine, start_year: int, end_year: int) -> List[str]:
    """获取 [start_year, end_year] 每月最后一个交易日（yyyymmdd 列表）。"""
    start_date = f"{start_year}0101"
    end_date = f"{end_year}1231"
    sql = f"""
    WITH monthly_trading AS (
        SELECT
            cal_date,
            YEAR(cal_date) as year,
            MONTH(cal_date) as month,
            MAX(cal_date) OVER (PARTITION BY YEAR(cal_date), MONTH(cal_date)) as last_trade_date
        FROM trade_cal
        WHERE cal_date >= {start_date}
          AND cal_date <= {end_date}
          AND is_open = 1
    )
    SELECT DISTINCT last_trade_date
    FROM monthly_trading
    WHERE cal_date = last_trade_date
    ORDER BY last_trade_date
    """
    result = pd.read_sql_query(sql, engine)
    return [d.strftime("%Y%m%d") for d in result["last_trade_date"].tolist()]
