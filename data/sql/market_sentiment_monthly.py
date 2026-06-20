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

class MarketSentimentMonthlyCalculator(BaseCalculator):
    """月度市场热度计算器（使用增量更新，不进行分批处理）"""
    
    def __init__(self, engine=None):
        """
        初始化月度市场热度计算器
        
        Args:
            engine: 数据库引擎，如果为None则使用基类的默认引擎
        """
        # 调用基类构造函数，支持自定义engine
        if engine is None:
            engine = global_engine
        
        # 调用基类构造函数，确保传递有效的engine
        super().__init__("MarketSentimentMonthlyCalculator", engine=engine)
        
        # 设置默认表名和写入模式
        self.default_table_name = "market_sentiment_monthly"
        self.default_write_mode = "overwrite"
        
        self.logger.info("MarketSentimentMonthlyCalculator初始化完成")
    
    def get_data(
        self, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None, 
        stock_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        获取月度市场热度计算所需数据（使用AVG直接计算日均值）
        """
        # 基础查询，在子查询中先限制日期范围
        query = """
        WITH ranked_daily_data AS (
            SELECT 
                ts_code,
                trade_date,
                DATE_FORMAT(trade_date, '%%Y-%%m-01') as month_start,
                open, high, low, close, amount, turnover_rate, vol,
                pct_chg,
                buy_lg_amount, buy_elg_amount, sell_lg_amount, sell_elg_amount,
                l1_name, total_mv, is_hs300, is_zz500, is_zz1000, is_zz2000,
                pe, pe_ttm, pb,
                -- 为每月第一个交易日标记
                ROW_NUMBER() OVER (
                    PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01')
                    ORDER BY trade_date
                ) as first_day_rn,
                -- 为每月最后一个交易日标记
                ROW_NUMBER() OVER (
                    PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01')
                    ORDER BY trade_date DESC
                ) as last_day_rn
            FROM stock_daily_wide 
            WHERE 1=1
        """
        
        # 为主表添加日期条件（基类已处理为yyyymmdd格式）
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        
        # 为主表添加股票代码条件（基类已处理entity_list）
        if stock_list:
            codes_str = ",".join([f"'{code}'" for code in stock_list])
            query += f" AND ts_code IN ({codes_str})"
        
        query += """
        ),
        ranked_percentiles_data AS (
            SELECT 
                ts_code,
                trade_date,
                DATE_FORMAT(trade_date, '%%Y-%%m-01') as month_start,
                price_tsrank_1y, pe_tsrank_1y, pe_ttm_tsrank_1y, pb_tsrank_1y,
                ma20, ma60, ma250, close,
                volatility_20, volatility_60, volatility_250,
                -- 为每月最后一个交易日标记
                ROW_NUMBER() OVER (
                    PARTITION BY ts_code, DATE_FORMAT(trade_date, '%%Y-%%m-01')
                    ORDER BY trade_date DESC
                ) as last_day_rn
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
        ),
        monthly_aggregates AS (
            -- 月度聚合数据（直接使用AVG计算日均值）
            SELECT 
                ts_code,
                month_start,
                MAX(trade_date) as last_trade_date,
                MIN(trade_date) as first_trade_date,
                MAX(high) as month_high,
                MIN(low) as month_low,
                AVG(amount) as avg_daily_amount,  -- 直接使用AVG计算日均成交额
                AVG(turnover_rate) as avg_turnover_rate,  -- 日均换手率
                AVG(vol) as avg_daily_volume,  -- 日均成交量
                AVG(buy_lg_amount + buy_elg_amount) as avg_daily_main_buy,  -- 日均主力买入
                AVG(sell_lg_amount + sell_elg_amount) as avg_daily_main_sell  -- 日均主力卖出
            FROM ranked_daily_data
            GROUP BY ts_code, month_start
        ),
        first_day_data AS (
            -- 每月第一个交易日的数据（开盘价）
            SELECT 
                ts_code,
                month_start,
                open as month_open
            FROM ranked_daily_data
            WHERE first_day_rn = 1
        ),
        last_day_data AS (
            -- 每月最后一个交易日的数据（收盘价、估值、维度等）
            SELECT 
                ts_code,
                month_start,
                close as month_close,
                l1_name as month_end_l1_name,
                total_mv as month_end_total_mv,
                is_hs300 as month_end_is_hs300,
                is_zz500 as month_end_is_zz500,
                is_zz1000 as month_end_is_zz1000,
                is_zz2000 as month_end_is_zz2000,
                pe as month_end_pe,
                pe_ttm as month_end_pe_ttm,
                pb as month_end_pb
            FROM ranked_daily_data
            WHERE last_day_rn = 1
        ),
        last_day_percentiles AS (
            -- 每月最后一个交易日的百分位数据
            SELECT 
                ts_code,
                month_start,
                price_tsrank_1y, pe_tsrank_1y, pe_ttm_tsrank_1y, pb_tsrank_1y,
                ma20, ma60, ma250, close as month_end_close,
                volatility_20, volatility_60, volatility_250
            FROM ranked_percentiles_data
            WHERE last_day_rn = 1
        )
        SELECT 
            ma.ts_code,
            ma.month_start,
            -- 将月份开始日期转换为月末自然日，因为每个股票每月最后一个交易日不一定能对齐，用自然日保证对齐
            LAST_DAY(ma.month_start) as month_end_natural,
            ma.last_trade_date,
            ma.first_trade_date,
            ma.month_high,
            ma.month_low,
            ma.avg_daily_amount,  -- 日均成交额
            ma.avg_turnover_rate,  -- 日均换手率
            ma.avg_daily_volume,  -- 日均成交量
            ma.avg_daily_main_buy,  -- 日均主力买入
            ma.avg_daily_main_sell,  -- 日均主力卖出
            fd.month_open,
            ld.month_close,
            ld.month_end_l1_name,
            ld.month_end_total_mv,
            ld.month_end_is_hs300,
            ld.month_end_is_zz500,
            ld.month_end_is_zz1000,
            ld.month_end_is_zz2000,
            CASE 
                WHEN ld.month_end_is_hs300 = 1 THEN '1.沪深300'
                WHEN ld.month_end_is_zz500 = 1 THEN '2.中证500' 
                WHEN ld.month_end_is_zz1000 = 1 THEN '3.中证1000'
                WHEN ld.month_end_is_zz2000 = 1 THEN '4.中证2000'
                ELSE '5.其他'
            END AS index_category,
            ld.month_end_pe,
            ld.month_end_pe_ttm,
            ld.month_end_pb,
            lp.price_tsrank_1y, lp.pe_tsrank_1y, lp.pe_ttm_tsrank_1y, lp.pb_tsrank_1y,
            lp.ma20, lp.ma60, lp.ma250, lp.month_end_close,
            lp.volatility_20, lp.volatility_60, lp.volatility_250
        FROM monthly_aggregates ma
        LEFT JOIN first_day_data fd ON ma.ts_code = fd.ts_code AND ma.month_start = fd.month_start
        LEFT JOIN last_day_data ld ON ma.ts_code = ld.ts_code AND ma.month_start = ld.month_start
        LEFT JOIN last_day_percentiles lp ON ma.ts_code = lp.ts_code AND ma.month_start = lp.month_start
        ORDER BY ma.month_start, ma.ts_code
        """
        
        self.logger.info(f"获取月度市场热度数据: {start_date or '开始'}~{end_date or '结束'}, "
                        f"股票数: {len(stock_list) if stock_list else '全部'}")
        
        try:
            data = pd.read_sql(query, self.engine)
            self.logger.info(f"成功获取 {len(data)} 条记录")
            return data
        except Exception as e:
            self.logger.error(f"获取月度数据失败: {e}")
            return pd.DataFrame()
    
    def process_data(
        self, 
        data: pd.DataFrame, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        处理月度市场热度数据（需要所有股票数据一起处理）
        """
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()
        
        self.logger.info(f"开始计算月度市场热度，输入数据 {len(data)} 条记录")
        
        # 数据预处理
        df = self._preprocess_data(data)
        
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
            self.logger.info(f"计算 {dim_type} 维度月度市场热度")
            dim_results = self._calculate_dimension_sentiment(df, dim_type, dim_col)
            if not dim_results.empty:
                results.append(dim_results)
                self.logger.info(f"{dim_type} 维度计算完成，生成 {len(dim_results)} 条记录")
        
        # 合并所有结果
        if results:
            final_result = pd.concat(results, ignore_index=True)
            
            # 确定最新月份（最后交易日期最大的月份）
            if not final_result.empty:
                # 找到最新的交易日期
                max_trade_date = final_result['last_trade_date'].max()
                # 找到对应的月份
                latest_month = final_result[final_result['last_trade_date'] == max_trade_date]['month_start'].iloc[0]
                
                # 对于最新月份，使用last_trade_date作为trade_date
                # 对于历史月份，使用月末自然日作为trade_date
                final_result['trade_date'] = final_result.apply(
                    lambda row: row['last_trade_date'] if row['month_start'] == latest_month else row['month_end_natural'],
                    axis=1
                )
            
            # 清理数据
            final_result = final_result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
            
            # 删除不需要的列
            columns_to_drop = ['month_start', 'month_end_natural', 'last_trade_date', 'first_trade_date']
            for col in columns_to_drop:
                if col in final_result.columns:
                    final_result = final_result.drop(col, axis=1)

            # 日期移到第一列
            if 'trade_date' in final_result.columns:
                cols = ['trade_date'] + [col for col in final_result.columns if col != 'trade_date']
                final_result = final_result[cols]
                
            self.logger.info(f"月度市场热度计算完成，共生成 {len(final_result)} 条记录")
            return final_result
        else:
            self.logger.warning("未生成任何月度市场热度记录")
            return pd.DataFrame()
    
    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """数据预处理"""
        # 处理缺失值和异常值
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
        
        # 计算月度涨跌幅（基于月度OHLC）：使用第一个交易日的开盘价和最后一个交易日的收盘价
        df['monthly_pct_chg'] = ((df['month_close'] - df['month_open']) / df['month_open'] * 100).fillna(0)
        
        # 计算月度振幅
        df['monthly_amplitude'] = ((df['month_high'] - df['month_low']) / df['month_open'] * 100).fillna(0)
        
        # 转换金额单位（万元转亿元）- 日均值也需要转换
        df['avg_daily_amount'] = df['avg_daily_amount'] / 10000
        df['avg_daily_main_buy'] = df['avg_daily_main_buy'] / 10000
        df['avg_daily_main_sell'] = df['avg_daily_main_sell'] / 10000
        
        # 计算日均主力净流入
        df['avg_daily_main_net_inflow'] = df['avg_daily_main_buy'] - df['avg_daily_main_sell']
        
        return df
    
    def _add_dimension_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """为股票添加维度标签"""
        df = df.copy()
        
        # 1. 市值维度切割 - 使用月末市值
        conditions = [
            df['month_end_total_mv'] < 200000,                    # <20亿
            (df['month_end_total_mv'] >= 200000) & (df['month_end_total_mv'] < 500000),      # 20-50亿
            (df['month_end_total_mv'] >= 500000) & (df['month_end_total_mv'] < 1000000),     # 50-100亿
            (df['month_end_total_mv'] >= 1000000) & (df['month_end_total_mv'] < 3000000),    # 100-300亿
            df['month_end_total_mv'] >= 3000000                  # >=300亿
        ]
        choices = ['1.<20亿', '2.20-50亿', '3.50-100亿', '4.100-300亿', '5.>=300亿']
        df['cap_category'] = np.select(conditions, choices, default='0.未知')
        
        # 2. 指数归属已在SQL中计算，直接使用index_category列
        # 3. 行业分类（使用月末行业）
        df['l1_name'] = df['month_end_l1_name'].fillna('未知行业')
        
        # 记录维度分布统计
        if not df.empty:
            cap_counts = df['cap_category'].value_counts()
            index_counts = df['index_category'].value_counts()
            industry_counts = df['l1_name'].value_counts()
            
            self.logger.info(f"月度市值分布: {dict(cap_counts)}")
            self.logger.info(f"月度指数分布: {dict(index_counts)}")
            self.logger.info(f"月度行业数量: {len(industry_counts)}个行业")
        
        return df
    
    def _calculate_dimension_sentiment(self, df: pd.DataFrame, dim_type: str, dim_col: str) -> pd.DataFrame:
        """计算单个维度的市场热度"""
        if df.empty:
            return pd.DataFrame()
        
        results = []
        
        # 按月份和维度分组
        grouped = df.groupby(['month_start', dim_col])
        
        for (month_start, dim_value), group in grouped:
            if len(group) < 3:  # 至少需要3只股票
                continue
                
            # 计算热度指标
            sentiment = self._calculate_single_group_sentiment(group, month_start, dim_type, str(dim_value))
            if sentiment:
                results.append(sentiment)
        
        return pd.DataFrame(results) if results else pd.DataFrame()
    
    def _calculate_single_group_sentiment(self, group: pd.DataFrame, month_start: str, 
                                        dim_type: str, dim_value: str) -> Optional[Dict]:
        """计算单个分组的热度指标（简化版，直接使用SQL计算出的日均值）"""
        try:
            result = {
                'month_start': month_start,
                'dimension_type': dim_type,
                'dimension_value': dim_value,
                'stock_count': len(group),
                'valid_count': group['monthly_pct_chg'].notna().sum(),
                'last_trade_date': group['last_trade_date'].max(),
                'month_end_natural': group['month_end_natural'].iloc[0]
            }
            
            if result['valid_count'] == 0:
                return None
            
            # 月度涨跌分布
            monthly_pct_chg = group['monthly_pct_chg'].dropna()
            
            # 计算涨跌阈值
            up_mask = monthly_pct_chg > 0      # 上涨
            strong_up_mask = monthly_pct_chg > 10  # 大涨 >10%
            down_mask = monthly_pct_chg < 0     # 下跌
            strong_down_mask = monthly_pct_chg < -10  # 大跌 <-10%
            
            result['up_ratio'] = round(up_mask.mean(), 4)
            result['strong_up_ratio'] = round(strong_up_mask.mean(), 4)
            result['down_ratio'] = round(down_mask.mean(), 4)
            result['strong_down_ratio'] = round(strong_down_mask.mean(), 4)
            
            # 涨跌幅度 - 使用月度涨跌幅
            result['avg_pct_chg'] = round(monthly_pct_chg.mean(), 4)
            
            # 上涨股票平均涨幅
            up_pct_chg = monthly_pct_chg[up_mask]
            result['avg_up_pct_chg'] = round(up_pct_chg.mean(), 4) if not up_pct_chg.empty else 0.0
            
            # 下跌股票平均跌幅
            down_pct_chg = monthly_pct_chg[down_mask]
            result['avg_down_pct_chg'] = round(down_pct_chg.mean(), 4) if not down_pct_chg.empty else 0.0
            
            # 成交活跃度（使用SQL计算出的日均值）
            turnover = group['avg_turnover_rate'].dropna()
            if not turnover.empty:
                result['avg_turnover_rate'] = round(turnover.mean(), 4)
            
            amount = group['avg_daily_amount'].dropna()
            if not amount.empty:
                result['total_amount'] = round(amount.sum(), 4)  # 维度内日均总成交额
                result['avg_amount'] = round(amount.mean(), 4)   # 平均每只股票日均成交额
            
            volume = group['avg_daily_volume'].dropna()
            if not volume.empty:
                result['total_volume'] = round(volume.sum(), 4)
                result['avg_volume'] = round(volume.mean(), 4)
            
            # 主力资金（使用SQL计算出的日均值）
            if all(col in group.columns for col in ['avg_daily_main_buy', 'avg_daily_main_sell', 'avg_daily_main_net_inflow']):
                main_buy = group['avg_daily_main_buy'].dropna()
                main_sell = group['avg_daily_main_sell'].dropna()
                net_inflow = group['avg_daily_main_net_inflow'].dropna()
                
                if not main_buy.empty:
                    result['main_buy_amount'] = round(main_buy.sum(), 4)  # 维度内日均主力买入总额
                if not main_sell.empty:
                    result['main_sell_amount'] = round(main_sell.sum(), 4)  # 维度内日均主力卖出总额
                if not net_inflow.empty and amount.sum() > 0:
                    result['main_net_inflow'] = round(net_inflow.sum(), 4)  # 维度内日均主力净流入
                    result['main_net_inflow_ratio'] = round(net_inflow.sum() / amount.sum(), 4)  # 主力净流入占比
            
            # 估值热度
            for pe_col, target_col in [('month_end_pe', 'avg_pe'), ('month_end_pe_ttm', 'avg_pe_ttm'), ('month_end_pb', 'avg_pb')]:
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
            if all(col in group.columns for col in ['month_end_close', 'ma20', 'ma60', 'ma250']):
                valid_data = group[['month_end_close', 'ma20', 'ma60', 'ma250']].dropna()
                if not valid_data.empty:
                    above_ma20 = (valid_data['month_end_close'] > valid_data['ma20']).mean()
                    above_ma60 = (valid_data['month_end_close'] > valid_data['ma60']).mean()
                    above_ma250 = (valid_data['month_end_close'] > valid_data['ma250']).mean()
                    
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
            self.logger.warning(f"计算月度分组热度失败: {e}")
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
        重写batch_process方法，月度市场热度计算不能分批处理
        直接调用incremental_update处理所有数据
        """
        self.logger.warning("月度市场热度计算不能使用分批处理，将使用增量更新方式处理所有数据")
        
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

