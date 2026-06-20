import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any, Union
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator
from data.utils.date_utils import get_next_n_trading_date

class ForwardReturnsCalculator(BaseCalculator):
    """未来收益率计算器（使用基类提供的批量处理和增量更新功能）"""
    
    def __init__(self,                  
                 holding_horizons: List[int] = None, 
                 engine=None):

        # 调用基类构造函数，支持自定义engine
        if engine is None:
            engine = global_engine
        
        # 调用基类构造函数，确保传递有效的engine
        super().__init__("ForwardReturnsCalculator", engine=engine)
        
        # 持有N天后卖出的收益
        self.holding_horizons = holding_horizons or [1, 3, 5, 10, 20, 40, 60]
        
        # 默认表名和写入模式
        self.default_table_name = 'stock_forward_returns'
        self.default_write_mode = 'overwrite'  # 一般是每日更新，用overwrite保证幂等，回补时用append
        
        self.logger.info(f"ForwardReturnsCalculator初始化完成，计算周期: {self.holding_horizons}")
    
    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取未来收益率计算所需数据，注意，这里的定义是个股交易持仓N天的收益率，而不是交易日历持仓N天，所以每支股票的实际持仓天数不同，在更新数据是要有一个冗余天数，
        例如更新N=60的收益率，要取一个更大的回看窗口，例如250天，以覆盖绝大多数停牌复牌的个股
        Args:
            start_date: 开始日期 (yyyymmdd格式)
            end_date: 结束日期 (yyyymmdd格式)
            entity_list: 股票代码列表
            **kwargs: 额外参数
        Returns:
            包含股票代码、交易日期、收盘价、VWAP、复权因子、单日收益率（用来计算标准差）的DataFrame
        """
        query = """
        SELECT ts_code, trade_date, close, vwap, adj_factor, pct_chg, is_st, market
        FROM stock_daily_wide 
        WHERE 1=1
        """
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'" 
        
        if entity_list:
            codes_str = ",".join([f"'{code}'" for code in entity_list])
            query += f" AND ts_code IN ({codes_str})"
            
        query += " ORDER BY ts_code, trade_date"
        
        self.logger.info(f"获取未来收益率数据: {start_date or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(entity_list) if entity_list else '全部'}")
        
        return pd.read_sql(query, self.engine)
    
    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:

        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()
        
        self.logger.info(f"开始计算持仓收益率，输入数据 {len(data)} 条记录")
        
        # 1. 数据预处理
        result = data.sort_values(['ts_code', 'trade_date']).copy()

        result['ret'] = result['pct_chg']/100
        result['is_st'] = result['is_st'].fillna(0).astype(bool)
        result['is_limit_up'] = self.is_limit_up_vectorized(
            result['is_st'].values, 
            result['market'].values, 
            result['pct_chg'].values
        )
        result['adj_vwap'] = result['adj_factor'] * result['vwap']

        # 2. 下一交易日，以及在往后N个交易日
        result['next_trade_date'] = result.groupby('ts_code')['trade_date'].shift(-1)
        result['is_next_date_limit_up'] = result.groupby('ts_code')['is_limit_up'].shift(-1)
        result['buy_price'] = result.groupby('ts_code')['adj_vwap'].shift(-1)
        
        # 持仓收益率
        for period in self.holding_horizons:
            result['sell_price_{}D'.format(period)] = result.groupby('ts_code')['adj_vwap'].shift(-1-period)
            result['{}D'.format(period)] = (result['sell_price_{}D'.format(period)]/result['buy_price']) -1
        # 收益率的标准差（10天以上才计算，太短的持仓期看标准差没意义）
        for period in self.holding_horizons:
            if period>=10:
                result['{}D_vol'.format(period)] = result.groupby('ts_code').apply(lambda x: x.rolling(period)['ret'].std().shift(-1-period)).values

        output_cols = ['ts_code', 'trade_date', 'next_trade_date', 'is_next_date_limit_up']
        
        for period in self.holding_horizons:
            output_cols.append(f'{period}D')

        for period in self.holding_horizons:
            if period>=10:
                output_cols.append(f'{period}D_vol')
        
        result = result[output_cols].copy()

        if start_date and end_date:
            start_date = start_date.replace('-', '')
            end_date = end_date.replace('-', '')
            mask = result['trade_date'].astype(str).str.replace('-','').between(start_date, end_date)
            result = result[mask]
                
            self.logger.info(f"过滤后数据范围: {start_date or '最早'} 到 {end_date or '最晚'}, "
                           f"记录数: {len(result)}")
        
        result = result.replace({np.nan: None,np.inf: None,-np.inf: None,pd.NaT: None})
        self.logger.info(f"持仓收益率计算完成，输出 {len(result)} 条记录")

        return result
        
    def is_limit_up_vectorized(self, is_st_array, market_array, pct_chg_array):
        """向量化涨停判断（批量处理，性能更好）"""
        # 转换为numpy数组
        is_st = np.array(is_st_array, dtype=bool)
        market = np.array(market_array, dtype=str)
        pct_chg = np.array(pct_chg_array, dtype=float)
        
        # 初始化涨停阈值数组
        limit_threshold = np.full_like(pct_chg, 20.0)  # 默认20%
        
        # 主板
        mask_main = (market == '主板')
        limit_threshold[mask_main & is_st] = 5.0   # 主板ST
        limit_threshold[mask_main & ~is_st] = 10.0  # 主板非ST
        
        # 创业板/科创板
        mask_gem_star = (market == '创业板') | (market == '科创板')
        limit_threshold[mask_gem_star] = 20.0
        
        # 北交所
        mask_bj = (market == '北交所')
        limit_threshold[mask_bj] = 30.0
        
        # 判断涨停
        return (pct_chg >= (limit_threshold - 0.05)).astype(int)
                
        # # 以下是按照交易日历持仓N天的收益率代码，日期是对齐的，但是个股的实际持仓周期会不同，也有可能有nan
        # df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
        
        # # 2. 计算日期锚点（避免重复计算）
        # unique_dates = pd.Series(df['trade_date'].unique()).sort_values()
        # date_anchors = pd.DataFrame({'trade_date': unique_dates})
        
        # # 计算下一个交易日
        # date_anchors['next_trade_date'] = date_anchors['trade_date'].apply(lambda x: get_next_n_trading_date(x))
        
        # # 计算各持仓周期的卖出日期
        # for period in self.holding_horizons:
        #     col_name = f'sell_date_{period}D'
        #     date_anchors[col_name] = date_anchors['next_trade_date'].apply(lambda x: get_next_n_trading_date(x, period))
        
        # self.logger.info(f"日期锚点计算完毕，共 {len(date_anchors)} 个交易日")
        
        # # 3. 合并日期锚点到原始数据
        # df = pd.merge(df, date_anchors, on='trade_date', how='left')
        
        # # 4. 计算调整后VWAP
        # df['adj_vwap'] = df['vwap'] * df['adj_factor']
        
        # # 5. 准备下一个交易日数据
        # # 选择下一交易日需要的列
        # next_day_cols = ['ts_code', 'trade_date', 'close', 'adj_vwap', 'pct_chg']
        # next_day_df = df[next_day_cols].copy()
        # next_day_df = next_day_df.rename(columns={
        #     'trade_date': 'next_trade_date',
        #     'close': 'close_next',
        #     'adj_vwap': 'adj_vwap_next',
        #     'pct_chg': 'pct_chg_next'
        # })
        
        # # 6. 合并下一交易日数据
        # merge_cols = ['ts_code', 'trade_date', 'next_trade_date', 'adj_vwap'] + \
        #              [f'sell_date_{period}D' for period in self.holding_horizons]
        
        # result = pd.merge(
        #     df[merge_cols],
        #     next_day_df,
        #     on=['ts_code', 'next_trade_date'],
        #     how='left',
        #     suffixes=('', '_next')
        # )
        
        # # 7. 判断下一个交易日条件
        # result['is_next_date_open'] = result['close_next'].notna().astype(int)
        
        # # 是否涨跌停，这里简单用9.5%作为阈值，后续可以改更复杂
        # result['is_next_date_limit'] = np.where(result['pct_chg_next'].abs() > 9.5, 1, 0)
        
        # # 8. 计算各持仓周期收益率
        # for period in self.holding_horizons:
        #     sell_date_col = f'sell_date_{period}D'
        #     return_col = f'{period}D'
            
        #     # 获取卖出日的调整后VWAP
        #     sell_price_df = df[['ts_code', 'trade_date', 'adj_vwap']].copy()
        #     sell_price_df = sell_price_df.rename(columns={
        #         'trade_date': sell_date_col,
        #         'adj_vwap': f'adj_vwap_{period}D'
        #     })
            
        #     # 合并卖出日价格
        #     result = pd.merge(
        #         result,
        #         sell_price_df,
        #         on=['ts_code', sell_date_col],
        #         how='left',
        #         suffixes=('', f'_{period}D')
        #     )
            
        #     # 计算收益率
        #     result[return_col] = (result[f'adj_vwap_{period}D'] / result['adj_vwap_next']) - 1
        
        # # 9. 选择最终输出列
        # output_cols = ['ts_code', 'trade_date', 'next_trade_date', 'is_next_date_open', 'is_next_date_limit']
        
        # for period in self.holding_horizons:
        #     output_cols.append(f'{period}D')
        
        # result = result[output_cols].copy()
        
        # # 10. 过滤日期范围
        # if start_date and end_date:
        #     start_str = start_date.replace('-', '') if start_date else None
        #     end_str = end_date.replace('-', '') if end_date else None
        #     mask = result['trade_date'].between(start_str, end_str)
        #     result = result[mask]
                
        #     self.logger.info(f"过滤后数据范围: {start_date or '最早'} 到 {end_date or '最晚'}, "
        #                    f"记录数: {len(result)}")
        
        # # 11. 转换日期格式
        # result['trade_date'] = pd.to_datetime(result['trade_date'], format='%Y%m%d').dt.date
        # result['next_trade_date'] = pd.to_datetime(result['next_trade_date'], format='%Y%m%d').dt.date
        
        # # 12. 清理无效值
        # result = result.replace({np.nan: None,np.inf: None,-np.inf: None,pd.NaT: None})
        
        # self.logger.info(f"持仓收益率计算完成，输出 {len(result)} 条记录")
        
        # return result

        # 以下是计算三重标签的代码，计算量太大，ROI不确定，暂时注释掉
        # def _apply_triple_barrier_labels(self, data: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
        #     """应用三重标签"""
        #     # 为每个三重标签配置处理
        #     for config in self.triple_barrier_configs:
        #         N = config['N']
        #         upper_return = config['upper_return']
        #         lower_return = config['lower_return']
        #         col_name = config['col_name']
                
        #         self.logger.info(f"计算三重标签: {col_name}, N={N}, 上轨={upper_return}, 下轨={lower_return}")
                
        #         # 转换为对数收益率
        #         upper_log_return = np.log(1 + upper_return)
        #         lower_log_return = np.log(1 + lower_return)
                
        #         # 为每个股票计算滚动窗口统计量，注意，是看观察日的未来N天的累计收益，因此先shift(-1-N)，再滚动
        #         def calculate_tb_stats(group):
        #             # 计算最大值和最小值，
        #             max_vals = group['log_return'].shift(-1-N).rolling(window=N, min_periods=1).apply(lambda x: np.cumsum(x).max())
        #             min_vals = group['log_return'].shift(-1-N).rolling(window=N, min_periods=1).apply(lambda x: np.cumsum(x).min())
        #             # 计算极值位置
        #             argmax_vals = group['log_return'].shift(-1-N).rolling(window=N, min_periods=1).apply(lambda x: np.cumsum(x).argmax())
        #             argmin_vals = group['log_return'].shift(-1-N).rolling(window=N, min_periods=1).apply(lambda x: np.cumsum(x).argmin())
                    
        #             return pd.DataFrame({
        #                 'max_val': max_vals,
        #                 'min_val': min_vals,
        #                 'argmax_val': argmax_vals,
        #                 'argmin_val': argmin_vals
        #             }, index=group.index)
                
        #         # 应用滚动计算
        #         tb_stats = data.groupby('ts_code',group_keys=False).apply(calculate_tb_stats).reset_index(level=0, drop=True)
                
        #         # 合并统计量到数据
        #         data_with_stats = data.join(tb_stats)
                
        #         # 应用三重标签逻辑
        #         def apply_tb_label(row):
        #             if pd.isna(row['max_val']) or pd.isna(row['min_val']):
        #                 return 0
                    
        #             hit_upper = row['max_val'] > upper_log_return
        #             hit_lower = row['min_val'] < lower_log_return
                    
        #             if not hit_upper and not hit_lower:
        #                 return 0
        #             elif hit_upper and not hit_lower:
        #                 return 1
        #             elif not hit_upper and hit_lower:
        #                 return -1
        #             else:
        #                 # 同时触碰，比较先后顺序
        #                 if pd.isna(row['argmax_val']) or pd.isna(row['argmin_val']):
        #                     return 0
        #                 if row['argmax_val'] < row['argmin_val']:
        #                     return 1
        #                 else:
        #                     return -1
                
        #         # 应用标签
        #         data_with_stats[col_name] = data_with_stats.apply(apply_tb_label, axis=1)
        #         # 将结果合并到最终结果
        #         result = result.merge(
        #             data_with_stats[['ts_code', 'trade_date', col_name]],
        #             on=['ts_code', 'trade_date'],
        #             how='left'
        #         )
                
        #         # 统计标签分布
        #         label_dist = result[col_name].value_counts().sort_index()
        #         self.logger.info(f"标签分布 {col_name}: {dict(label_dist)}")
            
        #     return result