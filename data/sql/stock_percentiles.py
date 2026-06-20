import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator
from data.utils.date_utils import get_previous_n_trading_date
                  
                         
## 历史百分位计算器
class StockPercentilesCalculator(BaseCalculator):
    """百分位计算器"""
    
    def __init__(self, engine=None):
        """
        初始化百分位计算器
        
        Args:
            engine: 数据库引擎，如果为None则使用基类的默认引擎
        """
        # 调用基类构造函数
        if engine is None:
            engine = global_engine
            
        super().__init__("PercentilesCalculator", engine=engine)
        
        # 设置计算参数
        self.lookback_1y = 250  # 1年交易日（250天）
        self.min_history_days = 120  # 最小历史数据天数，有的股票过去250天开盘数不足120，则不参与分位数计算
        
        # 设置默认表名和写入模式
        self.default_table_name = 'stock_percentiles'
        self.default_write_mode = 'overwrite'  # 一般是每日更新，用overwrite保证幂等
        
        self.logger.info("PercentilesCalculator初始化完成")
    
    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取股票日线数据
        """
            
        # 扩展日期范围以获取足够的历史数据
        extended_start = None
        if start_date:
            start_date = start_date.replace('-', '')
            # 获取前250个交易日，由于个股可能停牌，多取一点尽量覆盖
            extended_start = get_previous_n_trading_date(start_date, 250+100)
        else:
            extended_start = None
        
        query = """
        SELECT 
            ts_code, trade_date, close, pre_close, pct_chg,
            pe, pe_ttm, pb, turnover_rate, adj_factor
        FROM stock_daily_wide 
        WHERE 1=1
        """
        
        if extended_start:
            query += f" AND trade_date >= '{extended_start}'"
        if end_date:
            end_date = end_date.replace('-','')
            query += f" AND trade_date <= '{end_date}'"
        
        if entity_list:
            codes_str = ",".join([f"'{code}'" for code in entity_list])
            query += f" AND ts_code IN ({codes_str})"
            
        query += " ORDER BY ts_code, trade_date"
        
        self.logger.info(f"获取股票日线数据: {extended_start or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(entity_list) if entity_list else '全部'}")
        
        return pd.read_sql(query, self.engine)
    
    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        处理数据，计算百分位和技术指标（适配基类的yyyymmdd格式）
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return data
        
        self.logger.info(f"开始处理百分位数据，输入数据 {len(data)} 条记录")
        
        # 按股票分组处理
        results = []
        
        for ts_code, group in data.groupby('ts_code'):
            try:                    
                stock_result = self._process_single_stock(group)
                if start_date and end_date:
                    stock_result['trade_date_str'] = stock_result['trade_date'].astype(str).str.replace('-','')
                    stock_result = stock_result[stock_result['trade_date_str'].between(start_date, end_date)]
                    stock_result = stock_result.drop('trade_date_str', axis=1)
                
                if not stock_result.empty:
                    results.append(stock_result)
                            
            except Exception as e:
                self.logger.error(f"处理股票 {ts_code} 时出错: {e}")
                continue
        
        if results:
            final_result = pd.concat(results, ignore_index=True)
            self.logger.info(f"百分位数据处理完成: {len(final_result)} 条记录")
            
            final_result = final_result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
            
            return final_result
        else:
            self.logger.warning("百分位数据处理完成，但未生成有效记录")
            return pd.DataFrame()
                         
    
    def _process_single_stock(self, stock_data: pd.DataFrame) -> pd.DataFrame:
        """处理单只股票的数据）"""
        
        # 确保数据按日期排序
        stock_data = stock_data.sort_values('trade_date').copy()
        
        # 计算复权价
        stock_data['adj_close'] = stock_data['close'] * stock_data['adj_factor']
        
        # 计算技术指标
        stock_data = self._calculate_technical_indicators(stock_data)
        
        # 计算滚动百分位
        stock_data = self._calculate_rolling_percentiles(stock_data)
        
        # 选择需要的列
        result_columns = [
            'ts_code', 'trade_date', 'close', 'pe', 'pe_ttm', 'pb', 
            'turnover_rate', 'pct_chg', 
            'price_tsrank_1y', 'pe_tsrank_1y', 'pe_ttm_tsrank_1y', 'pb_tsrank_1y',
            'ma20', 'ma60', 'ma250',
            'volatility_20', 'volatility_60', 'volatility_250'
        ]
        
        # 确保所有列都存在
        available_columns = [col for col in result_columns if col in stock_data.columns]
        
        return stock_data[available_columns]
    
    def _calculate_technical_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标：移动平均线和波动率"""
        
        # 计算移动平均线
        data['ma20'] = data['close'].rolling(20).mean()
        data['ma60'] = data['close'].rolling(60).mean()
        data['ma250'] = data['close'].rolling(250).mean()
        
        # 计算收益波动率
        data['volatility_20'] = data['pct_chg'].rolling(20).std()
        data['volatility_60'] = data['pct_chg'].rolling(60).std()
        data['volatility_250'] = data['pct_chg'].rolling(250).std()
        
        return data
    
    def _calculate_rolling_percentiles(self, data: pd.DataFrame) -> pd.DataFrame:
        """使用滚动窗口计算百分位"""
        # 定义要计算百分位的指标
        metrics = [
            ('price', 'adj_close'),
            ('pe', 'pe'),
            ('pe_ttm', 'pe_ttm'),
            ('pb', 'pb')
        ]
        
        # 计算1年百分位
        for metric_name, source_col in metrics:
            if source_col not in data.columns:
                continue
                
            # 1年百分位
            data[f'{metric_name}_tsrank_1y'] = data[source_col].rolling(
                window=self.lookback_1y,
                min_periods=self.min_history_days
            ).apply(
                lambda x: self._calculate_percentile_in_window(x, self.lookback_1y), 
                raw=True
            )
        
        return data
    
    def _calculate_percentile_in_window(self, window_values, lookback_days):
        """计算当前值在滚动窗口中的百分位"""
        min_periods = self.min_history_days
        
        if len(window_values) < min_periods or np.isnan(window_values[-1]):
            return np.nan
        
        try:
            # 当前值是窗口的最后一个值
            current_value = window_values[-1]
            # 历史值是窗口的前n-1个值
            historical_values = window_values[:-1]
            
            # 移除NaN值
            historical_values = historical_values[~np.isnan(historical_values)]
            
            if len(historical_values) < min_periods:
                return np.nan
            
            # 计算当前值在历史值中的百分位
            percentile = stats.percentileofscore(historical_values, current_value, kind='mean') / 100.0
            return percentile
        except:
            return np.nan
    