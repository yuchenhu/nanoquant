import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any
from itertools import product
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator
from data.utils.date_utils import get_previous_n_trading_date

class StockDailyWideCalculator(BaseCalculator):
    """股票日线宽表计算器（继承基类，只保留核心功能）"""
    
    def __init__(self, engine=None, index_lookback_window: int = 40):
        """
        初始化股票日线宽表计算器
        
        Args:
            engine: 数据库引擎，如果为None则使用基类的默认引擎
            index_lookback_window: 指数成分回看窗口天数，指数成分一般月末更新（见index_weight），取40，保证能取到本月+前一个月
        """
        # 调用基类构造函数，支持自定义engine
        if engine is None:
            engine = global_engine
            
        super().__init__("StockDailyWideCalculator", engine=engine)
        
        # 设置计算参数
        self.index_lookback_window = index_lookback_window
        
        # 设置默认表名和写入模式
        self.default_table_name = 'stock_daily_wide'
        self.default_write_mode = 'overwrite'  # 一般是每日更新，用overwrite保证幂等
        
        self.logger.info(f"StockDailyWideCalculator初始化完成，指数回看窗口: {index_lookback_window}天")
    
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
        query = "SELECT * FROM stock_daily WHERE 1=1"
        
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
        """
        处理股票日线数据为宽表格式
        
        Args:
            data: 基础日线数据
            start_date: 开始日期 (用于查询辅助数据)
            end_date: 结束日期 (用于查询辅助数据)
            **kwargs: 额外参数
            
        Returns:
            宽表格式的股票日线数据
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return data
        
        self.logger.info(f"开始处理股票日线宽表，输入数据 {len(data)} 条记录")
        
        # 1. 处理基础日线数据
        result = self._process_daily_data(data)
        
        # 2. 连接复权因子
        result = self._join_adj_factor(result, start_date, end_date)
        
        # 3. 连接ST信息
        result = self._join_st_info(result, start_date, end_date)
        
        # 4. 连接停牌信息
        result = self._join_suspend_info(result, start_date, end_date)
        
        # 5. 连接每日指标
        result = self._join_daily_basic(result, start_date, end_date)
        
        # 6. 连接资金流向
        result = self._join_moneyflow(result, start_date, end_date)
        
        # 7. 连接股票基本信息
        result = self._join_stock_basic(result)
        
        # 8. 连接指数成员信息
        result = self._join_index_member(result)
        
        # 9. 连接指数权重信息
        result = self._join_index_weight(result, start_date, end_date)
        
        self.logger.info(f"股票日线宽表处理完成，输出数据 {len(result)} 条记录")

        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        
        return result
    
    def _process_daily_data(self, daily_data: pd.DataFrame) -> pd.DataFrame:
        """处理基础日线数据"""
        result = daily_data.copy()
        
        # amount转换为万元单位，原本是千元
        result['amount'] = result['amount'] / 10
        
        # 计算log_return: ln(1 + pct_chg/100)
        result['log_return'] = np.log(1 + result['pct_chg'] / 100)
        
        # 计算VWAP: 成交额/成交量 (注意单位转换)
        result['vwap'] = result['amount'] * 10000 / (result['vol'] * 100)  # amount是万元，vol是手
        
        return result
    
    def _join_adj_factor(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """连接复权因子"""
        query = "SELECT ts_code, trade_date, adj_factor FROM adj_factor WHERE 1=1"
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
            
        adj_factor = pd.read_sql(query, self.engine)
        
        if not adj_factor.empty:
            data = data.merge(adj_factor, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"复权因子合并成功: {len(data[data['adj_factor'].notna()])} 条记录")
        else:
            data['adj_factor'] = 1.0
            self.logger.warning("未找到复权因子数据，使用默认值1.0")
        
        return data
    
    def _join_st_info(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """连接ST信息"""
        query = """
        SELECT ts_code, trade_date, 
               CASE WHEN type IS NOT NULL THEN 1 ELSE 0 END as is_st
        FROM stock_st 
        WHERE 1=1
        """
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
            
        st_info = pd.read_sql(query, self.engine)
        
        if not st_info.empty:
            data = data.merge(st_info, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"ST信息合并成功: {len(data[data['is_st'].notna()])} 条记录")
        else:
            data['is_st'] = 0
            self.logger.warning("未找到ST信息数据，使用默认值0")
        
        return data
    
    def _join_suspend_info(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """连接停牌信息"""
        query = """
        SELECT ts_code, trade_date, 
               MAX(CASE WHEN suspend_type='S' THEN 1 ELSE 0 END) as is_suspend
        FROM suspend 
        WHERE 1=1
        """
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
            
        query += " GROUP BY ts_code, trade_date"
        
        suspend_info = pd.read_sql(query, self.engine)
        
        if not suspend_info.empty:
            data = data.merge(suspend_info, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"停牌信息合并成功: {len(data[data['is_suspend'].notna()])} 条记录")
        else:
            data['is_suspend'] = 0
            self.logger.warning("未找到停牌信息数据，使用默认值0")
        
        return data
    
    def _join_daily_basic(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """连接每日指标"""
        query = """
        SELECT ts_code, trade_date, turnover_rate, turnover_rate_f, volume_ratio,
               pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_share,
               float_share, free_share, total_mv, circ_mv
        FROM stock_daily_basic 
        WHERE 1=1
        """
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
            
        daily_basic = pd.read_sql(query, self.engine)
        
        if not daily_basic.empty:
            data = data.merge(daily_basic, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"每日指标合并成功: {len(data[data['turnover_rate'].notna()])} 条记录")
        else:
            self.logger.warning("未找到每日指标数据")
        
        return data
    
    def _join_moneyflow(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """连接资金流向"""
        query = "SELECT * FROM moneyflow WHERE 1=1"
        
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
            
        moneyflow = pd.read_sql(query, self.engine)
        
        if not moneyflow.empty:
            # 移除可能重复的列
            existing_columns = set(data.columns)
            new_columns = [col for col in moneyflow.columns if col not in existing_columns or col in ['ts_code', 'trade_date']]
            moneyflow = moneyflow[new_columns]
            
            data = data.merge(moneyflow, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"资金流向合并成功: {len(data[data['buy_sm_vol'].notna()]) if 'buy_sm_vol' in moneyflow.columns else 0} 条记录")
        else:
            self.logger.warning("未找到资金流向数据")
        
        return data
    
    def _join_stock_basic(self, data: pd.DataFrame) -> pd.DataFrame:
        """连接股票基本信息"""
        query = """
        SELECT ts_code, market, exchange, list_status, list_date, delist_date, is_hs 
        FROM stock_basic
        """
        
        stock_basic = pd.read_sql(query, self.engine)
        
        if not stock_basic.empty:
            data = data.merge(stock_basic, on='ts_code', how='left')
            
            # 计算上市天数
            if 'list_date' in data.columns and 'trade_date' in data.columns:
                data['list_days'] = (pd.to_datetime(data['trade_date']) - 
                                   pd.to_datetime(data['list_date'])).dt.days
            
            self.logger.info(f"股票基本信息合并成功: {len(data[data['market'].notna()])} 条记录")
        else:
            self.logger.warning("未找到股票基本信息数据")
        
        return data
    
    def _join_index_member(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        连接指数成员信息
        为每个交易日找到之前最近的in_date对应的行业信息
        """
        query = "SELECT l1_code, l1_name, l2_code, l2_name, ts_code, in_date FROM index_member_all"
        
        index_member = pd.read_sql(query, self.engine)
                
        # 转换日期格式
        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        index_member['in_date_dt'] = pd.to_datetime(index_member['in_date'])
        
        # 确保数据按股票代码和入市日期排序
        index_member_sorted = index_member.sort_values(['in_date_dt'])
        data_sorted = data.sort_values(['trade_date_dt'])
        
        # 使用merge_asof找到每个交易日之前最近的in_date
        try:
            merged = pd.merge_asof(
                data_sorted,
                index_member_sorted,
                left_on='trade_date_dt',
                right_on='in_date_dt',
                by='ts_code',
                direction='backward'
            )
            
            merged = merged[['ts_code', 'trade_date_dt', 'l1_code', 'l1_name', 'l2_code', 'l2_name']]
            
            # 使用左连接将结果合并回原始数据
            data = data.merge(
                merged,
                on=['ts_code', 'trade_date_dt'],
                how='left',
                suffixes=('', '')
            )
            
        except Exception as e:
            self.logger.error(f"使用merge_asof合并行业信息失败: {e}")
        
        # 清理临时列
        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
        
        # 统计匹配成功的记录数
        matched_count = data['l1_code'].notna().sum()
        self.logger.info(f"指数成员信息合并成功: {matched_count} 条记录匹配行业信息")
            
        return data
    
    def _join_index_weight(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
        """
        连接指数权重信息，指数成分是月度表，大部分每月最后一个交易日更新，也有的每月第一个/最后一个交易日，因此要回看一段时间就近匹配，保证有数据
        使用笛卡尔积，构建指数成分X指数调仓日期，再用merge_asof向前匹配最近一次调仓日期
        """
        # 定义目标指数
        target_indexes = {
            'is_hs300': '399300.SZ',
            'is_zz500': '000905.SH', 
            'is_zz800': '000906.SH',
            'is_zz1000': '000852.SH',
            'is_zz2000': '932000.CSI',
            'is_zzhl': '000922.CSI',
            'is_hldb': '930955.CSI'
        }
    
        # 初始化结果列
        for col in target_indexes.keys():
            data[col] = 0
    
        # 获取指数权重数据
        index_codes = list(target_indexes.values())
        index_codes_str = ",".join([f"'{code}'" for code in index_codes])
    
        query = f"""
        SELECT index_code, con_code, trade_date
        FROM index_weight 
        WHERE index_code IN ({index_codes_str}) 
        """

        # 计算指数数据的开始日期
        if start_date:
            start_date = start_date.replace('-','')
            start_date_dt = datetime.strptime(start_date, '%Y%m%d')
            index_start_date = (start_date_dt - timedelta(days=self.index_lookback_window)).strftime('%Y%m%d')
            query += f" AND trade_date >= '{index_start_date}'"
            
        if end_date:
            end_date = end_date.replace('-','')
            query += f" AND trade_date <= '{end_date}'"

        query += "ORDER BY index_code, trade_date, con_code"

        index_weight = pd.read_sql(query, self.engine)
    
        if index_weight.empty:
            self.logger.warning("未找到指数权重数据")
            return data
    
        # 转换日期格式
        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        index_weight['trade_date_dt'] = pd.to_datetime(index_weight['trade_date'])

        # 为每个指数单独处理
        for target_col, index_code in target_indexes.items():
            self.logger.info(f"处理指数 {index_code} ({target_col})")
    
            # 获取该指数的数据
            index_data = index_weight[index_weight['index_code'] == index_code].copy()
            if index_data.empty:
                self.logger.warning(f"指数 {index_code} 无数据")
                continue

            # 按月去重：只保留每月第一天和最后一天记录（早期指数成分是日频的，数据量太大，2012以后数据可以注释掉这段节省开销）
            # index_data['year_month'] = index_data['trade_date_dt'].dt.to_period('M')
            
            # # 找到每个月的最大日期（即每月最后一条记录）
            # monthly_first = index_data.groupby(['year_month'])['trade_date_dt'].min()
            # monthly_last = index_data.groupby(['year_month'])['trade_date_dt'].max()
            
            # # 合并第一天和最后一天的日期
            # monthly_key_dates = pd.concat([monthly_first, monthly_last]).drop_duplicates()
            
            # # 只保留每月的最新记录
            # index_data = index_data[index_data['trade_date_dt'].isin(monthly_key_dates)]
            
            self.logger.info(f"按月去重后，指数 {index_code} 有 {len(index_data)} 条记录")
            
            # 获取调整日期
            adjustment_dates = sorted(index_data['trade_date_dt'].unique())
            
            # 获取所有股票
            all_stocks = data['ts_code'].unique()
            
            # 创建笛卡尔积：所有股票 x 所有调整日期
            # 创建股票DataFrame
            stocks_df = pd.DataFrame({'ts_code': all_stocks})
            stocks_df['key'] = 1  # 用于笛卡尔积的键
            
            # 创建调整日期DataFrame
            dates_df = pd.DataFrame({'adjustment_date': adjustment_dates})
            dates_df['key'] = 1  # 用于笛卡尔积的键
            
            # 创建笛卡尔积
            cartesian_df = stocks_df.merge(dates_df, on='key').drop('key', axis=1)
            
            # 标记成分股状态
            # 重命名index_data的列以匹配
            index_data_renamed = index_data[['con_code', 'trade_date_dt']].rename(
                columns={'con_code': 'ts_code', 'trade_date_dt': 'adjustment_date'}
            )
            index_data_renamed['is_component'] = 1
            
            # 左连接笛卡尔积和指数数据
            temp_df = cartesian_df.merge(
                index_data_renamed, 
                on=['ts_code', 'adjustment_date'], 
                how='left'
            )
            
            # 填充NaN值为0
            temp_df['is_component'] = temp_df['is_component'].fillna(0)
            
            # 确保数据正确排序
            data_sorted = data.sort_values(['trade_date_dt']).copy()
            temp_df_sorted = temp_df.sort_values(['adjustment_date']).copy()
            
            # 使用merge_asof，通过by参数按股票代码分组
            try:
                merged = pd.merge_asof(
                    data_sorted,
                    temp_df_sorted,
                    left_on='trade_date_dt',
                    right_on='adjustment_date',
                    by='ts_code',  # 按股票代码分组
                    direction='backward'
                )
                
                # 使用左连接将结果合并回原始数据
                data = data.merge(
                    merged[['ts_code', 'trade_date_dt', 'is_component']],
                    on=['ts_code', 'trade_date_dt'],
                    how='left'
                )
                
                # 重命名列并填充0
                data[target_col] = data['is_component'].fillna(0).astype(int)
                data.drop('is_component', axis=1, inplace=True)  # 删除临时列

            except Exception as e:
                self.logger.error(f"使用merge_asof失败: {e}")
    
        # 清理临时列
        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
    
        return data
