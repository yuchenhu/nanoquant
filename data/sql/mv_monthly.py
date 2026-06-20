import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any
from itertools import product
from sqlalchemy import text
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator

class MvMonthlyCalculator(BaseCalculator):
    
    def __init__(self, engine=None, lookback_years=5):

        # 调用基类构造函数
        if engine is None:
            engine = global_engine
            
        super().__init__("MvMonthlyCalculator", engine=engine)
        self.default_table_name = 'mv_monthly'
        self.default_write_mode = 'overwrite'
        
        self.logger.info(f"MvMonthlyCalculator初始化完成")

    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取股票日线数据
        
        Args:
            start_date: 开始日期 (yyyymmdd格式)
            end_date: 结束日期 (yyyymmdd格式)
            entity_list: 股票代码列表
            **kwargs: 额外参数
            
        Returns:
            基础日线数据DataFrame
        """
        query = """
        SELECT ts_code, trade_date, total_mv, circ_mv, total_share, float_share,
        ROW_NUMBER() over (partition by ts_code, DATE_FORMAT(trade_date, '%%Y-%%m') order by trade_date desc) as rn
        FROM stock_daily_basic WHERE 1=1
        """
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        
        if entity_list:
            codes_str = ",".join([f"'{code}'" for code in entity_list])
            query += f" AND ts_code IN ({codes_str})"
            
        query += " ORDER BY ts_code, trade_date"
        
        self.logger.info(f"获取股票日线数据: {start_date or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(entity_list) if entity_list else '全部'}")
        
        return pd.read_sql(query, self.engine)

    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:

        result = data[data.rn==1]
        result = result.drop('rn',axis=1)
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        
        return result