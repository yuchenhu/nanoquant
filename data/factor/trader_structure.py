import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Tuple, Optional, Any
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator
from data.utils.date_utils import get_previous_n_trading_date
from data.utils.preprocessing import *

class TraderStructureCalculator(BaseCalculator):
    
    def __init__(self, engine=None):

        # 调用基类构造函数
        if engine is None:
            engine = global_engine
            
        super().__init__("TraderStructureCalculator", engine=engine)
        
        self.lookback_period = 40
        
        # 设置默认表名和写入模式
        self.default_table_name = 'trader_structure'
        self.default_write_mode = 'overwrite'  # 一般是每日更新，用overwrite保证幂等
        
        self.logger.info("TraderStructureCalculator初始化完成")
    
    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取股票日线数据（适配基类的yyyymmdd格式）
        """
            
        extended_start = None
        if start_date:
            start_date = start_date.replace('-', '')
            extended_start = get_previous_n_trading_date(start_date, self.lookback_period)
        else:
            extended_start = None

        if end_date:
            end_date = end_date.replace('-', '')
        
        stock_query = """
        SELECT 
            ts_code, trade_date, pct_chg/100 as ret, vol, turnover_rate_f, 
            buy_sm_vol, sell_sm_vol, buy_md_vol, sell_md_vol, buy_lg_vol, sell_lg_vol, buy_elg_vol, sell_elg_vol, 
            l1_code, l1_name, l2_code, l2_name
        FROM stock_daily_wide 
        WHERE 1=1
        """
        
        if extended_start:
            stock_query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            stock_query += f" AND trade_date <= '{end_date}'"
        if entity_list:
            codes_str = ",".join([f"'{code}'" for code in entity_list])
            stock_query += f" AND ts_code IN ({codes_str})"
        
        self.logger.info(f"获取股票日线数据: {extended_start or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(entity_list) if entity_list else '全部'}")
        
        stock_df = pd.read_sql(stock_query, self.engine)

        return stock_df

    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        处理数据，计算量价因子
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()
        
        df = data.copy()
        df = df[df.turnover_rate_f>0]
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df['vm_ret'] = df['vol'] * df['ret']

        df['buy_sm_vol_ratio'] = df['buy_sm_vol'] / df['vol']
        df['buy_elg_vol_ratio'] = df['buy_elg_vol'] / df['vol']

        df['sm_vol'] = df['buy_sm_vol'] + df['sell_sm_vol']
        df['net_sm_vol'] = df['buy_sm_vol'] - df['sell_sm_vol']
        df['sm_imba'] = df['net_sm_vol'] / df['sm_vol']

        df['elg_vol'] = df['buy_elg_vol'] + df['sell_elg_vol']
        df['net_elg_vol'] = df['buy_elg_vol'] - df['sell_elg_vol']
        df['elg_imba'] = df['net_elg_vol'] / df['elg_vol']

        ##和其他切割变量统一成过去40个交易日要求有20天开盘且有资金流数据
        self.logger.info(f"原始数据共{len(df)}行")
        df['open_days'] = df.groupby('ts_code')['ret'].transform('count')
        df['moneyflow_days'] = df.groupby('ts_code')['buy_sm_vol'].transform('count')
        df = df[(df.open_days>=20)&(df.moneyflow_days>=20)]
        df = df.sort_values(by=['ts_code','trade_date'], ascending=[True, False]).reset_index(drop=True)
        df = df.groupby('ts_code').head(20)
        self.logger.info(f"剔除后数据共{len(df)}行")

        result = pd.DataFrame()
        result['trade_date'] = df.groupby('ts_code')['trade_date'].max()

        ##按照交易者结构切割涨跌幅
        df = df.sort_values(by=['ts_code', 'buy_sm_vol_ratio'], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby('ts_code')
        for i in range(20):
            result[f'buy_sm_{i}_ret'] = group['ret'].nth(i)

        df = df.sort_values(by=['ts_code', 'buy_elg_vol_ratio'], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby('ts_code')
        for i in range(20):
            result[f'buy_elg_{i}_ret'] = group['ret'].nth(i)

        df = df.sort_values(by=['ts_code', 'sm_imba'], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby('ts_code')
        for i in range(20):
            result[f'sm_imba_{i}_ret'] = group['ret'].nth(i)

        df = df.sort_values(by=['ts_code', 'elg_imba'], ascending=[True, False]).reset_index(drop=True)
        group = df.groupby('ts_code')
        for i in range(20):
            result[f'elg_imba_{i}_ret'] = group['ret'].nth(i)

        ##根据数据分析结果，加工top5/top10等权、线性衰减、指数衰减三种聚合方式
        for prefix in ['buy_sm', 'buy_elg', 'sm_imba', 'elg_imba']:
            for n in [5, 10]:
                linear_weights = list(np.arange(1,0,-1/n))
                exp_weights = [np.power(2,-i/(n-1)) for i in np.arange(n)]

                result[f'{prefix}_top{n}'] = result[[f'{prefix}_{i}_ret' for i in range(n)]].sum(axis=1)
                result[f'{prefix}_top{n}_ld'] = result[[f'{prefix}_{i}_ret' for i in range(n)]].dot(linear_weights)
                result[f'{prefix}_top{n}_exp'] = result[[f'{prefix}_{i}_ret' for i in range(n)]].dot(exp_weights)
                
                result[f'{prefix}_bottom{n}'] = result[[f'{prefix}_{20-n+i}_ret' for i in range(n)]].sum(axis=1)
                result[f'{prefix}_bottom{n}_ld'] = result[[f'{prefix}_{20-n+i}_ret' for i in range(n)]].dot(linear_weights[::-1])
                result[f'{prefix}_bottom{n}_exp'] = result[[f'{prefix}_{20-n+i}_ret' for i in range(n)]].dot(exp_weights[::-1])
                
        ##过滤掉当天不开盘的股票
        if end_date:
            result['trade_date_str'] = result['trade_date'].astype(str).str.replace('-', '')
            result = result[result['trade_date_str']==end_date]
            result=result.drop('trade_date_str',axis=1)
        self.logger.info(f"聚合指标计算完成，输出数据 {len(result)} 条记录")

        result = result.reset_index()
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)

        return result