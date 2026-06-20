import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator

class MarketSentimentDailyCalculator(BaseCalculator):
    """市场热度计算器（使用增量更新，不进行分批处理）"""
    
    def __init__(self, engine=None):
        """
        初始化市场热度计算器
        
        Args:
            engine: 数据库引擎，如果为None则使用基类的默认引擎
        """
        # 调用基类构造函数，支持自定义engine
        if engine is None:
            engine = global_engine
        
        # 调用基类构造函数，确保传递有效的engine
        super().__init__("MarketSentimentCalculator", engine=engine)
        
        # 设置默认表名和写入模式
        self.default_table_name = "market_sentiment_daily"
        self.default_write_mode = "overwrite"
        
        self.logger.info("MarketSentimentDailyCalculator初始化完成")
    
    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        stock_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取市场热度计算所需数据（在子查询中先限制日期范围）
        """
        # 基础查询，在子查询中先限制日期范围
        query = """
        SELECT 
            w.ts_code, w.trade_date, w.pct_chg, w.turnover_rate, w.amount,
            w.pe, w.pe_ttm, w.pb, w.total_mv,
            w.buy_lg_amount, w.buy_elg_amount, w.sell_lg_amount, w.sell_elg_amount,
            w.l1_name,
            w.is_hs300, w.is_zz500, w.is_zz1000, w.is_zz2000,
            -- 使用与之前一致的指数分类逻辑
            CASE 
                WHEN w.is_hs300 = 1 THEN '1.沪深300'
                WHEN w.is_zz500 = 1 THEN '2.中证500' 
                WHEN w.is_zz1000 = 1 THEN '3.中证1000'
                WHEN w.is_zz2000 = 1 THEN '4.中证2000'
                ELSE '5.其他'
            END AS index_category,
            p.price_tsrank_1y, p.pe_tsrank_1y, p.pe_ttm_tsrank_1y, p.pb_tsrank_1y,
            p.ma20, p.ma60, p.ma250,
            p.volatility_20, p.volatility_60, p.volatility_250,
            -- 添加close字段用于计算均线位置
            w.close
        FROM (
            SELECT * 
            FROM stock_daily_wide 
            WHERE 1=1
        """
        
        # 为主表添加日期条件
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        
        # 为主表添加股票代码条件
        if stock_list:
            codes_str = ",".join([f"'{code}'" for code in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        
        query += """
        ) w
        LEFT JOIN (
            SELECT *
            FROM stock_percentiles 
            WHERE 1=1
        """
        
        # 为百分位表添加日期条件
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        
        # 为百分位表添加股票代码条件
        if stock_list:
            codes_str = ",".join([f"'{code}'" for code in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        
        query += """
        ) p 
        ON w.ts_code = p.ts_code AND w.trade_date = p.trade_date
        ORDER BY w.trade_date, w.ts_code
        """
        
        self.logger.info(f"获取市场热度数据: {start_date or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(stock_list) if stock_list else '全部'}")
        
        try:
            data = pd.read_sql(query, self.engine)
            self.logger.info(f"成功获取 {len(data)} 条记录")
            return data
        except Exception as e:
            self.logger.error(f"获取数据失败: {e}")
            return pd.DataFrame()
    
    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        处理市场热度数据（需要所有股票数据一起处理）
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()
        
        self.logger.info(f"开始计算市场热度，输入数据 {len(data)} 条记录")
        
        # 数据预处理
        df = data.copy()
        df = self._preprocess_data(df)
        
        # 为每个股票打上维度标签
        df = self._add_dimension_labels(df)
        
        # 定义三个维度
        dimensions = [
            ('cap', 'cap_category'),
            ('index', 'index_category'), 
            ('industry', 'l1_name')
        ]
        
        results = []
        
        # 对每个维度分别计算热度指标
        for dim_type, dim_col in dimensions:
            self.logger.info(f"计算 {dim_type} 维度市场热度")
            dim_results = self._calculate_dimension_sentiment(df, dim_type, dim_col)
            if not dim_results.empty:
                results.append(dim_results)
                self.logger.info(f"{dim_type} 维度计算完成，生成 {len(dim_results)} 条记录")
        
        # 合并所有结果
        if results:
            final_result = pd.concat(results, ignore_index=True)
            
            # 过滤只保留指定日期范围的数据
            if start_date and end_date:
                # 确保日期格式一致
                final_result['trade_date_str'] = final_result['trade_date'].astype(str).str.replace('-','')
                final_result = final_result[final_result['trade_date_str'].between(start_date, end_date)]
                final_result = final_result.drop('trade_date_str', axis=1)
                
                self.logger.info(f"过滤后数据范围: {start_date} 到 {end_date}, 记录数: {len(final_result)}")
            
            final_result = final_result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
            self.logger.info(f"市场热度计算完成，共生成 {len(final_result)} 条记录")
            return final_result
        else:
            self.logger.warning("未生成任何市场热度记录")
            return pd.DataFrame()
    
    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """数据预处理"""
        # 处理缺失值和异常值
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
        
        # 转换金额单位（万元转亿元）
        df['amount'] = df['amount'] / 10000
        
        # 计算主力资金（万元转亿元）
        df['main_buy_amount'] = (df['buy_lg_amount'] + df['buy_elg_amount']).fillna(0) / 10000
        df['main_sell_amount'] = (df['sell_lg_amount'] + df['sell_elg_amount']).fillna(0) / 10000
        df['main_net_inflow'] = df['main_buy_amount'] - df['main_sell_amount']
        
        return df
    
    def _add_dimension_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """为股票添加维度标签"""
        df = df.copy()
        
        # 1. 市值维度切割 - 使用固定阈值
        conditions = [
            df['total_mv'] < 200000,                    # <20亿
            (df['total_mv'] >= 200000) & (df['total_mv'] < 500000),      # 20-50亿
            (df['total_mv'] >= 500000) & (df['total_mv'] < 1000000),     # 50-100亿
            (df['total_mv'] >= 1000000) & (df['total_mv'] < 3000000),    # 100-300亿
            df['total_mv'] >= 3000000                  # >=300亿
        ]
        choices = ['1.<20亿', '2.20-50亿', '3.50-100亿', '4.100-300亿', '5.>=300亿']
        df['cap_category'] = np.select(conditions, choices, default='0.未知')
        
        # 2. 指数归属已在SQL中计算，直接使用index_category列
        # 3. 一级行业（使用l1_name）
        df['l1_name'] = df['l1_name'].fillna('未知行业')
        
        # 记录维度分布统计
        if not df.empty:
            cap_counts = df['cap_category'].value_counts()
            index_counts = df['index_category'].value_counts()
            industry_counts = df['l1_name'].value_counts()
            
            self.logger.info(f"市值分布: {dict(cap_counts)}")
            self.logger.info(f"指数分布: {dict(index_counts)}")
            self.logger.info(f"行业数量: {len(industry_counts)}个行业")
        
        return df
    
    def _calculate_dimension_sentiment(self, df: pd.DataFrame, dim_type: str, dim_col: str) -> pd.DataFrame:
        """计算单个维度的市场热度"""
        if df.empty:
            return pd.DataFrame()
        
        results = []
        
        # 按日期和维度分组
        grouped = df.groupby(['trade_date', dim_col])
        
        for (trade_date, dim_value), group in grouped:
            if len(group) < 3:  # 至少需要3只股票
                continue
                
            # 计算热度指标
            sentiment = self._calculate_single_group_sentiment(group, trade_date, dim_type, str(dim_value))
            if sentiment:
                results.append(sentiment)
        
        return pd.DataFrame(results) if results else pd.DataFrame()
    
    def _calculate_single_group_sentiment(self, group: pd.DataFrame, trade_date: str, 
                                        dim_type: str, dim_value: str) -> Optional[Dict]:
        """计算单个分组的热度指标（包含所有表结构中的指标）"""
        try:
            result = {
                'trade_date': trade_date,
                'dimension_type': dim_type,
                'dimension_value': dim_value,
                'stock_count': len(group),
                'valid_count': group['pct_chg'].notna().sum()
            }
            
            if result['valid_count'] == 0:
                return None
            
            # 涨跌分布
            pct_chg = group['pct_chg'].dropna()
            
            # 计算涨跌阈值
            up_mask = pct_chg > 0.5      # 上涨（>0.5%）
            strong_up_mask = pct_chg > 5 # 大涨（>5%）
            down_mask = pct_chg < -0.5   # 下跌（<-0.5%）
            strong_down_mask = pct_chg < -5 # 大跌（<-5%）
            flat_mask = (pct_chg >= -0.5) & (pct_chg <= 0.5) # 平盘（-0.5%~0.5%）
            
            result['up_ratio'] = round(up_mask.mean(), 4)
            result['strong_up_ratio'] = round(strong_up_mask.mean(), 4)
            result['down_ratio'] = round(down_mask.mean(), 4)
            result['strong_down_ratio'] = round(strong_down_mask.mean(), 4)
            result['flat_ratio'] = round(flat_mask.mean(), 4)
            
            # 涨跌幅度
            result['avg_pct_chg'] = round(pct_chg.mean(), 4)
            
            # 上涨股票平均涨幅
            up_pct_chg = pct_chg[up_mask]
            result['avg_up_pct_chg'] = round(up_pct_chg.mean(), 4) if not up_pct_chg.empty else 0.0
            
            # 下跌股票平均跌幅
            down_pct_chg = pct_chg[down_mask]
            result['avg_down_pct_chg'] = round(down_pct_chg.mean(), 4) if not down_pct_chg.empty else 0.0
            
            # 成交活跃度
            turnover = group['turnover_rate'].dropna()
            if not turnover.empty:
                result['avg_turnover_rate'] = round(turnover.mean(), 4)
            
            amount = group['amount'].dropna()
            if not amount.empty:
                result['total_amount'] = round(amount.sum(), 4)
                result['avg_amount'] = round(amount.mean(), 4)
            
            # 主力资金
            if all(col in group.columns for col in ['main_buy_amount', 'main_sell_amount', 'main_net_inflow']):
                main_buy = group['main_buy_amount'].dropna()
                main_sell = group['main_sell_amount'].dropna()
                net_inflow = group['main_net_inflow'].dropna()
                
                if not main_buy.empty:
                    result['main_buy_amount'] = round(main_buy.sum(), 4)
                if not main_sell.empty:
                    result['main_sell_amount'] = round(main_sell.sum(), 4)
                if not net_inflow.empty and amount.sum() > 0:
                    result['main_net_inflow'] = round(net_inflow.sum(), 4)
                    result['main_net_inflow_ratio'] = round(net_inflow.sum() / amount.sum(), 4)
            
            # 估值热度
            for pe_col, target_col in [('pe', 'avg_pe'), ('pe_ttm', 'avg_pe_ttm'), ('pb', 'avg_pb')]:
                if pe_col in group.columns:
                    values = group[pe_col].replace([np.inf, -np.inf], np.nan).dropna()
                    values = values[(values > 0) & (values < 1000)]
                    if not values.empty:
                        result[target_col] = round(values.mean(), 4)
            
            # 百分位热度 - 1年（250日）
            percentile_1y_cols = {
                'price_tsrank_1y': 'avg_price_tsrank_1y',
                'pe_tsrank_1y': 'avg_pe_tsrank_1y',
                'pe_ttm_tsrank_1y': 'avg_pe_ttm_tsrank_1y',
                'pb_tsrank_1y': 'avg_pb_tsrank_1y'
            }
            
            for src_col, target_col in percentile_1y_cols.items():
                if src_col in group.columns:
                    values = group[src_col].dropna()
                    values = values[(values >= 0) & (values <= 1)]
                    if not values.empty:
                        result[target_col] = round(values.mean(), 4)
            
            # 波动率
            volatility_cols = {
                'volatility_20': 'avg_volatility_20',
                'volatility_60': 'avg_volatility_60',
                'volatility_250': 'avg_volatility_250'
            }
            for src_col, target_col in volatility_cols.items():
                if src_col in group.columns:
                    values = group[src_col].dropna()
                    values = values[values >= 0]
                    if not values.empty:
                        result[target_col] = round(values.mean(), 4)
            
            # 均线位置
            if all(col in group.columns for col in ['close', 'ma20', 'ma60', 'ma250']):
                valid_data = group[['close', 'ma20', 'ma60', 'ma250']].dropna()
                if not valid_data.empty:
                    # 计算站上均线的比例
                    above_ma20 = (valid_data['close'] > valid_data['ma20']).mean()
                    above_ma60 = (valid_data['close'] > valid_data['ma60']).mean()
                    above_ma250 = (valid_data['close'] > valid_data['ma250']).mean()
                    
                    result.update({
                        'above_ma20_ratio': round(above_ma20, 4),
                        'below_ma20_ratio': round(1 - above_ma20, 4),
                        'above_ma60_ratio': round(above_ma60, 4),
                        'below_ma60_ratio': round(1 - above_ma60, 4),
                        'above_ma250_ratio': round(above_ma250, 4),
                        'below_ma250_ratio': round(1 - above_ma250, 4)
                    })
            
            return result
            
        except Exception as e:
            self.logger.warning(f"计算分组热度失败: {e}")
            return None
    
    def batch_process(
        self,
        entity_list: Optional [List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        batch_size: int = 100,
        auto_save: bool = False,
        table_name: str = None,
        write_mode: str = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        重写batch_process方法，市场热度计算不能分批处理
        直接调用incremental_update处理所有数据
        """
        self.logger.warning("市场热度计算不能使用分批处理，将使用增量更新方式处理所有数据")
        
        # 使用默认值
        table_name = table_name or self.default_table_name
        write_mode = write_mode or self.default_write_mode
        
        # 直接调用incremental_update，忽略分批逻辑
        return self.incremental_update(
            start_date=start_date,
            end_date=end_date,
            auto_save=auto_save,
            table_name=table_name,
            write_mode=write_mode,
            **kwargs
        )
