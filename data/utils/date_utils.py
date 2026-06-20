import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import os
import logging

logger = logging.getLogger(__name__)

# 复用 data.config.database 的 engine，避免重复建连 + 重复硬编码配置
# 注意：旧版本曾在此硬编码 DB 密码 + 自建 engine，已改为复用全局 engine
from data.config.database import engine, execute_sql  # noqa: E402

# 缓存交易日历用于其他函数
global _TRADE_CAL_DF

_TRADE_CAL_DF = pd.read_sql("SELECT cal_date, is_open FROM trade_cal ORDER BY cal_date", engine)
_TRADE_CAL_DF['cal_date'] = _TRADE_CAL_DF['cal_date'].astype(str).apply(lambda x: x.replace('-', ''))

logger.info(f"交易日历缓存已加载，共 {len(_TRADE_CAL_DF)} 条记录")

def get_today_str(format_str: str = '%Y%m%d') -> str:
    """获取今天日期字符串"""
    return datetime.now().strftime(format_str)

def get_recent_quarter_dates(date_str: Optional[str] = None, n: int = 4) -> List[str]:
    """
    获取指定日期前几个季度的季度末日期
    
    Args:
        date_str: 计算日期 (yyyymmdd格式)，如果为None则使用今天
        n: 往前看的季度数，默认4个季度
    
    Returns:
        List[str]: 季度末日期列表 (yyyymmdd格式)，按时间顺序从早到晚排列
    """
    # 确定计算日期
    calc_date = datetime.now() if date_str is None else datetime.strptime(date_str, '%Y%m%d')
    
    # 季度末日期映射
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    
    # 获取当前季度
    current_quarter = (calc_date.month - 1) // 3 + 1
    current_year = calc_date.year
    
    # 确定起始季度（如果当前季度末已过，从当前季度开始，否则从上季度开始）
    q_month, q_day = quarter_ends[current_quarter]
    start_quarter = current_quarter if datetime(current_year, q_month, q_day) <= calc_date else current_quarter - 1
    start_year = current_year
    
    # 调整起始年份
    if start_quarter == 0:
        start_quarter = 4
        start_year -= 1
    
    # 生成季度末日期
    quarter_dates = []
    quarter, year = start_quarter, start_year
    
    for _ in range(n):
        month, day = quarter_ends[quarter]
        quarter_end = datetime(year, month, day)
        
        if quarter_end <= calc_date:
            quarter_dates.append(quarter_end.strftime('%Y%m%d'))
        
        # 移动到上一个季度
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    
    return sorted(quarter_dates)

def get_month_start_end(date_str: str = None) -> Dict[str, str]:
    """获取指定日期所在月份的第一天和最后一天（自然日，交易日可以通过交易日历简单获取）"""
    if date_str:
        # 处理不同格式的日期字符串
        if '-' in date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            target_date = datetime.strptime(date_str, '%Y%m%d')
    else:
        target_date = datetime.now()
    
    # 月份第一天
    month_start = datetime(target_date.year, target_date.month, 1)
    
    # 月份最后一天
    if target_date.month == 12:
        next_month = datetime(target_date.year + 1, 1, 1)
    else:
        next_month = datetime(target_date.year, target_date.month + 1, 1)
    
    month_end = next_month - timedelta(days=1)
    
    return {
        'start_date': month_start.strftime('%Y%m%d'),
        'end_date': month_end.strftime('%Y%m%d')
    }


def get_recent_weekday(date_str: Optional[str] = None) -> str: 
    """
    获取最近一个周的最后交易日（使用全局交易日历）
    规则：
    1. 查找输入日期所在周的最后交易日
    2. 如果最后交易日 <= 输入日期，则返回这个交易日
    3. 如果最后交易日 > 输入日期，输入日期-7，再次查找
    4. 如果循环52周都没找到，返回交易日历的最大交易日期
    Args:
        date_str: 日期字符串，格式为yyyymmdd
    Returns:
        最近一个周的最后交易日字符串(格式:yyyymmdd)，如果未找到则返回None
    """
    if date_str is None:
        date_str = get_today_str()
    input_date = datetime.strptime(date_str, '%Y%m%d')
    
    # 检查输入的日期是否在交易日历范围内
    trade_dates = _TRADE_CAL_DF[_TRADE_CAL_DF['is_open'] == 1]['cal_date'].tolist()
    trade_dates_set = set(trade_dates)
    
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)
    
    if date_str < min_trade_date:
        print(f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日 {min_trade_date}")
        return min_trade_date
    if date_str > max_trade_date:
        print(f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日 {max_trade_date}")
        return max_trade_date
    
    # 开始查找
    original_input_date = input_date
    original_input_str = date_str
    
    # 最大循环次数为52周，避免溢出
    for week in range(52):
        year, week_num, weekday = input_date.isocalendar()
        monday = input_date - timedelta(days=weekday-1)
        week_dates = []
        for i in range(7):
            date_obj = monday + timedelta(days=i)
            week_dates.append(date_obj.strftime('%Y%m%d'))

        week_trading_days = [d for d in week_dates if d in trade_dates_set]
        
        if week_trading_days:
            last_trade_day = max(week_trading_days)
            if last_trade_day <= original_input_str:
                return last_trade_day
        else:
            pass
        
        # 不满足条件，向前推一周
        input_date = input_date - timedelta(days=7)
    
    print(f"循环52周未找到符合条件的交易日，返回最大交易日期: {max_trade_date}")
    return max_trade_date

    
def get_recent_month(date_str: Optional[str] = None) -> str:
    """
    获取最近一个月的最后交易日
    规则：
    1. 查找输入日期所在月的所有交易日，找到最后一个交易日
    2. 如果这个交易日 <= 输入日期，则返回这个交易日
    3. 如果这个交易日 > 输入日期，输入日期的月份数-1，再次查找
    4. 如果循环12个月都没找到，返回交易日历的最大交易日期
    
    Args:
        date_str: 日期字符串，格式为yyyymmdd，如果为None则使用今天
        
    Returns:
        最近一个月的最后交易日字符串(格式:yyyymmdd)
    """
    # 如果未提供日期，使用今天
    if date_str is None:
        date_str = get_today_str()
    input_date = datetime.strptime(date_str, '%Y%m%d')
    
    # 获取交易日列表
    trade_dates = _TRADE_CAL_DF[_TRADE_CAL_DF['is_open'] == 1]['cal_date'].tolist()
    trade_dates_set = set(trade_dates)
    
    # 获取交易日历的最大和最小交易日期
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)
    
    # 检查输入日期是否在交易日历范围内
    if date_str < min_trade_date:
        print(f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日 {min_trade_date}")
        return min_trade_date
    if date_str > max_trade_date:
        print(f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日 {max_trade_date}")
        return max_trade_date
    
    # 开始查找
    current_date = input_date
    original_input_str = date_str
    
    # 最大循环次数为12个月
    for month in range(12):
        # 获取当前日期所在年份和月份
        year = current_date.year
        month_num = current_date.month
        
        # 获取该月的第一天和最后一天
        if month_num == 12:
            next_month_first_day = datetime(year + 1, 1, 1)
        else:
            next_month_first_day = datetime(year, month_num + 1, 1)
        
        month_first_day = datetime(year, month_num, 1)
        month_last_day = next_month_first_day - timedelta(days=1)
        
        # 生成该月的所有日期
        month_dates = []
        current_day = month_first_day
        while current_day <= month_last_day:
            month_dates.append(current_day.strftime('%Y%m%d'))
            current_day += timedelta(days=1)
        
        # 在该月日期中查找交易日
        month_trading_days = [d for d in month_dates if d in trade_dates_set]
        
        if month_trading_days:
            last_trade_day_in_month = max(month_trading_days)
            if last_trade_day_in_month <= original_input_str:
                return last_trade_day_in_month
        
        # 不满足条件，向前推一个月
        current_date = current_date.replace(day=1)  # 先转到当月第一天
        current_date = current_date - timedelta(days=1)  # 转到上个月最后一天
        current_date = current_date.replace(day=1)  # 转到上个月第一天
    
    # 如果循环了12个月还没找到，返回交易日历的最大交易日期
    print(f"循环12个月未找到符合条件的交易日，返回最大交易日期: {max_trade_date}")
    return max_trade_date
    
def is_trading_day(date_str: Optional[str] = None) -> bool:

    if date_str is None:
        date_str = get_today_str()
    
    # 在交易日历中查找该日期
    date_row = _TRADE_CAL_DF[_TRADE_CAL_DF['cal_date'] == date_str]
    
    if not date_row.empty:
        # 如果找到该日期，检查is_open是否为1
        return date_row['is_open'].iloc[0] == 1
    else:
        # 如果没有找到该日期，简单判断周末
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        if date_obj.weekday() in [5, 6]:  # 5=周六, 6=周日
            return False
        else:
            return True
        
def find_nearest_trading_day(date_str: Optional[str] = None, backward: bool = True) -> Optional[str]:
    """
    查找最近的交易日
    
    Args:
        date_str: 起始日期(YYYYMMDD)
        backward: 是否向后查找(True)或向前查找(False)
    
    Returns:
        str: 最近的交易日(YYYYMMDD)
    """
    if date_str is None:
        date_str = get_today_str()

    target_date = datetime.strptime(date_str, '%Y%m%d')

    # 获取交易日列表
    trade_dates = _TRADE_CAL_DF[_TRADE_CAL_DF['is_open'] == 1]['cal_date'].tolist()
    trade_dates_set = set(trade_dates)
    
    # 获取交易日历的最大和最小交易日期
    max_trade_date = max(trade_dates)
    min_trade_date = min(trade_dates)
    
    # 检查输入日期是否在交易日历范围内
    if date_str < min_trade_date:
        print(f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日 {min_trade_date}")
        return min_trade_date
    if date_str > max_trade_date:
        print(f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日 {max_trade_date}")
        return max_trade_date
        
    # 确定查找方向
    if backward:
        # 向后查找（更早的日期）
        search_dates = _TRADE_CAL_DF[
            (_TRADE_CAL_DF['cal_date'] <= date_str) & 
            (_TRADE_CAL_DF['is_open'] == 1)
        ].sort_values('cal_date', ascending=False)
    else:
        # 向前查找（更晚的日期）
        search_dates = _TRADE_CAL_DF[
            (_TRADE_CAL_DF['cal_date'] >= date_str) & 
            (_TRADE_CAL_DF['is_open'] == 1)
        ].sort_values('cal_date')
    
    if not search_dates.empty:
        return search_dates.iloc[0]['cal_date']
    else:
        return None
    
def get_previous_n_trading_date(date_str: Optional[str] = None, n_days: int = 1) -> Optional[str]:
    """
    计算输入日期往前推N个交易日的日期
    
    Args:
        date_str: 输入日期，格式为yyyymmdd
        n_days: 往前推的交易天数
        
    Returns:
        str: 往前推N个交易日的日期（yyyymmdd格式），如果找不到则返回None
    """
    if date_str is None:
        date_str = get_today_str()
        
    trade_cal=_TRADE_CAL_DF[_TRADE_CAL_DF.is_open==1]
    trade_cal = trade_cal.sort_values('cal_date').reset_index(drop=True)

    # 检查输入日期是否在交易日历范围内
    min_trade_date = trade_cal['cal_date'].min()
    max_trade_date = trade_cal['cal_date'].max()
    
    if date_str < min_trade_date:
        print(f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日 {min_trade_date}")
        return min_trade_date
    if date_str > max_trade_date:
        print(f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日 {max_trade_date}")
        return max_trade_date
        
    # 查找输入日期在交易日历中的位置
    date_mask = trade_cal['cal_date'] <= date_str
    dates_before_input = trade_cal[date_mask]
    dates_before_input = dates_before_input.sort_values('cal_date', ascending=False)

    if dates_before_input.empty:
        print (f"输入日期 {date_str} 之前没有交易日")
        return None

    if date_str == dates_before_input.iloc[0]['cal_date']:
        # 输入日期是交易日，从前一个交易日开始计数
        target_idx = n_days
    else:
        # 输入日期不是交易日，从第一个小于输入日期的交易日开始计数
        target_idx = n_days - 1

    # 检查索引是否在有效范围内
    if target_idx < len(dates_before_input):
        result_date = dates_before_input.iloc[target_idx]['cal_date']
        return result_date
    else:
        print(f"无法往前推 {n_days} 个交易日，最早只能到 {min_trade_date}")
        return min_trade_date

def get_next_n_trading_date(date_str: Optional[str] = None, n_days: int = 1) -> Optional[str]:
    """
    计算输入日期往后推N个交易日的日期
    
    Args:
        date_str: 输入日期，格式为yyyymmdd
        n_days: 往后推的交易天数
        
    Returns:
        str: 往后推N个交易日的日期（yyyymmdd格式），如果找不到则返回None
    """
    if date_str is None:
        date_str = get_today_str()
    
    # 获取所有交易日并按日期排序
    trade_cal = _TRADE_CAL_DF[_TRADE_CAL_DF['is_open'] == 1]
    trade_cal = trade_cal.sort_values('cal_date').reset_index(drop=True)

    # 检查输入日期是否在交易日历范围内
    min_trade_date = trade_cal['cal_date'].min()
    max_trade_date = trade_cal['cal_date'].max()

    if date_str < min_trade_date:
        print(f"输入日期 {date_str} 小于最小交易日 {min_trade_date}，返回最小交易日 {min_trade_date}")
        return min_trade_date
    if date_str > max_trade_date:
        print(f"输入日期 {date_str} 大于最大交易日 {max_trade_date}，返回最大交易日 {max_trade_date}")
        return max_trade_date
        
    # 查找大于等于输入日期的第一个交易日位置
    date_mask = trade_cal['cal_date'] >= date_str
    dates_from_input = trade_cal[date_mask]
    
    if dates_from_input.empty:
        print(f"输入日期 {date_str} 之后没有交易日")
        return max_date
    
    # 获取输入日期之后的第N个交易日索引
    if date_str == dates_from_input.iloc[0]['cal_date']:
        # 输入日期是交易日，从下一个交易日开始计数
        target_idx = n_days
    else:
        # 输入日期不是交易日，从第一个大于输入日期的交易日开始计数
        target_idx = n_days - 1
    
    # 检查索引是否在有效范围内
    if target_idx < len(dates_from_input):
        result_date = dates_from_input.iloc[target_idx]['cal_date']
        return result_date
    else:
        print(f"无法往后推 {n_days} 个交易日，最晚只能到 {max_trade_date}")
        return max_trade_date
        
def get_monthly_last_tradedate(engine, start_year, end_year):

    start_date=str(start_year)+'0101'
    end_date=str(end_year)+'1231'
    
    # SQL查询：获取每月最后一个交易日
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
    result=pd.read_sql_query(sql,engine)
    return [d.strftime('%Y%m%d') for d in result['last_trade_date'].tolist()]