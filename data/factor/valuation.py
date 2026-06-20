import pandas as pd
import numpy as np
import numba
from scipy import stats
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import logging
from sqlalchemy import text
from typing import List, Dict, Tuple, Optional, Any
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import save_to_database
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator

class ValuationFactorCalculator(BaseCalculator):
    """估值因子计算器"""
    
    def __init__(self, engine=None):
        """初始化估值因子计算器"""
        if engine is None:
            engine = global_engine
            
        super().__init__("ValuationFactorCalculator", engine=engine)
        
        self.default_table_name = 'valuation_factor'
        self.default_write_mode = 'overwrite'
        
        self.lookback_months=12
        self.all_cols = [
            'snapshot_date','ts_code','end_date','actual_date','total_mv',\
            'bp','rep','sp_q','gpp_q','ep_q','admp_q','rdp_q','taxp_q','ocfp_q',\
            'sp_ttm','gpp_ttm','ep_ttm','admp_ttm','rdp_ttm','taxp_ttm','ocfp_ttm','divp_ttm',\
        ]
        
        self.logger.info("ValuationFactorCalculator 初始化完成")

    def get_data(self, snapshot_date: str, entity_list: Optional[List[str]] = None, **kwargs) -> pd.DataFrame:

        query = """
        SELECT t.* FROM
        (
        SELECT {all_cols},
        row_number() over (partition by snapshot_date, ts_code order by end_date desc) AS rn
        FROM financial_indicators_snapshot 
        WHERE 1=1
        """

        query = query.format(all_cols=",".join(self.all_cols))
        
        if snapshot_date:
            start_date = datetime.strptime(snapshot_date, "%Y%m%d") - relativedelta(months=self.lookback_months)
            start_date = start_date.strftime("%Y%m%d")
            query += f" AND snapshot_date > '{start_date}'"
            query += f" AND snapshot_date <= '{snapshot_date}'"
        
        if entity_list:
            codes_str = ",".join([f"'{code}'" for code in entity_list])
            query += f" AND ts_code IN ({codes_str})"
            
        query += " ORDER BY ts_code, end_date"
        query += " ) WHERE t.rn=1"
        
        self.logger.info(f"获取财务指标数据: {snapshot_date or '全部'}, "
                        f"股票数: {len(entity_list) if entity_list else '全部'}")
        
        return pd.read_sql(query, self.engine)

    def process_data(self, data: pd.DataFrame, snapshot_date: str, **kwargs) -> pd.DataFrame:

        data = data.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
        data['arp_q'] = data['admp_q'].fillna(0)+data['rdp_q'].fillna(0)
        data['artp_q']= data['admp_q'].fillna(0)+data['rdp_q'].fillna(0)+data['taxp_q']
        data['arp_ttm'] = data['admp_ttm'].fillna(0)+data['rdp_ttm'].fillna(0)
        data['artp_ttm']= data['admp_ttm'].fillna(0)+data['rdp_ttm'].fillna(0)+data['taxp_ttm']

        #截取最新12期财报
        last12 = data.groupby('ts_code').tail(12).reset_index(drop=True)

        # Step 1：累计费用列用0填充（nan视为当期无费用）
        # last12.loc[:, self.sum_cols] = last12.loc[:, self.sum_cols].fillna(0)

        # Step 2：负数统计列先打标，方便分组统计
        for col in self.neg_cnt_cols:
            last12[f'{col}_neg'] = (last12[col]<0).astype(int)
        
        # Step 3：近12期统计列预先计算分位数
        for col in self.tsrank_12_cols:
            last12[f'{col}_tsrank_12'] = last12.groupby('ts_code')[col].rank(pct=True)

        # group好避免重复计算
        group = last12.groupby('ts_code')
        
        result = pd.DataFrame()
        result['report_cnt_12'] = group['actual_date'].count()

        result[self.latest_cols] = group[self.latest_cols].last()
        
        for col in self.tsrank_12_cols:
            mean = group[col].mean()
            std = group[col].std()
            result[f'{col}_msr_12'] = mean.div(std, np.nan)
            result[f'{col}_zs_12'] = (result[col] - mean).div(std, np.nan)
            result[f'{col}_tsrank_12'] = group[f'{col}_tsrank_12'].last()

        for col in self.neg_cnt_cols:
            result[f'{col}_neg_cnt_12'] = group[f'{col}_neg'].sum()

        # for col in self.sum_cols:
        #     result[f'{col}_sum_12_p'] = group[f'{col}'].sum().div(group['total_mv'].last(), np.nan)
        #     result[f'{col}_ld_12_p'] = group[f'{col}'].apply(self._get_linear_decay).div(group['total_mv'].last(), np.nan)

        # 再截取最近9期
        last9 = last12.groupby('ts_code').tail(9).reset_index(drop=True)
        
        for col in self.tsrank_9_cols:
            last9[f'{col}_tsrank_9'] = last9.groupby('ts_code')[col].rank(pct=True)
            
        group = last9.groupby('ts_code')
        
        for col in self.tsrank_9_cols:
            mean = group[col].mean()
            std = group[col].std()
            result[f'{col}_msr_9'] = mean.div(std, np.nan)
            result[f'{col}_zs_9'] = (result[col] - mean).div(std, np.nan)
            result[f'{col}_tsrank_9'] = group[f'{col}_tsrank_9'].last()

        result = result.reset_index()
        result['snapshot_date'] = pd.to_datetime(snapshot_date)
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        
        other_cols = [col for col in result.columns if col not in ['snapshot_date', 'ts_code']]
        new_col_order = ['snapshot_date', 'ts_code'] + other_cols
        
        return result[new_col_order]

    def _get_linear_decay(self, series, n=12):
        """
        计算线性衰减加权和
        
        对series最近n个值进行加权求和，权重是1/n, 2/n, 3/n, ..., n/n
        最近的值权重最大
        
        当序列长度小于n时，仍然使用权重[1/n, 2/n, ..., n/n]，
        但只取最后k个权重（从n-k+1到n）与最后k个值对应
        """
        m = len(series)
        k = min(m, n)
        
        if k == 0:
            return np.nan
        
        last_k = series.tail(k).values
        weights = np.arange(1, n + 1) / n
        last_k_weights = weights[-k:]
        
        # 对最后k个值应用权重
        weighted_sum = np.sum(last_k * last_k_weights)
        
        return weighted_sum

    def incremental_update(
        self,
        snapshot_date: str,
        auto_save: bool = True,
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        财务指标增量更新，只接受一个snapshot_date参数
        """
        self.logger.info(f"财务数据增量更新: {snapshot_date}")
        
        # 1. 调用子类自己的get_data方法获取数据
        # 注意：子类的get_data期望snapshot_date是yyyymmdd格式
        data = self.get_data(snapshot_date=snapshot_date, entity_list=entity_list, **kwargs)
        
        if data.empty:
            self.logger.error(f"获取{snapshot_date}数据失败")
            return pd.DataFrame()
        
        # 2. 调用子类自己的process_data方法处理数据
        result = self.process_data(data=data, snapshot_date=snapshot_date, **kwargs)
        
        if result.empty:
            self.logger.error(f"处理{snapshot_date}数据失败")
            return pd.DataFrame()
        
        # 3. 自动保存到数据库
        if auto_save and not result.empty:
            try:
                # 调用重写的save_to_database方法
                self.save_to_database(
                    data=result,
                    table_name=self.default_table_name,
                    write_mode=self.default_write_mode,
                    start_date=snapshot_date,
                    end_date=snapshot_date
                )
                self.logger.info(f"数据已自动保存到 {self.default_table_name}")
            except Exception as e:
                self.logger.error(f"保存数据到数据库失败: {e}")
        
        return result

    def save_to_database(
        self, 
        data: pd.DataFrame, 
        table_name: str = None, 
        write_mode: str = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> None:
        """
        保存数据到数据库（支持overwrite模式，针对财务数据使用snapshot_date）
        重写父类方法，将trade_date改为snapshot_date
        """
        # 使用默认值
        table_name = table_name or self.default_table_name
        write_mode = write_mode or self.default_write_mode
        
        # 处理overwrite模式
        if write_mode == 'overwrite':
            if start_date is None or end_date is None:
                raise ValueError("overwrite模式必须提供start_date和end_date参数")
            
            start_date = start_date.replace('-','')
            end_date = end_date.replace('-','')
            
            # 检查snapshot_date列是否存在
            if 'snapshot_date' not in data.columns:
                self.logger.error("DataFrame中没有snapshot_date列，无法执行overwrite模式")
                raise ValueError("财务数据必须包含snapshot_date列")
            
            # 先删除指定日期范围内的数据
            try:
                # 使用text()包装SQL语句
                delete_sql = text(f"""
                    DELETE FROM {table_name} 
                    WHERE snapshot_date BETWEEN :start_date AND :end_date
                """)
                
                with self.engine.begin() as conn:
                    result = conn.execute(delete_sql, {
                        'start_date': start_date, 
                        'end_date': end_date
                    })
                    deleted_count = result.rowcount
                    
                self.logger.info(f"overwrite模式: 已删除{table_name}中{start_date}到{end_date}的数据，影响行数: {deleted_count}")
                
            except Exception as e:
                self.logger.error(f"删除数据失败: {e}")

            write_mode = 'append'
        
        # 使用database.py中的save_to_database函数
        success = save_to_database(data, table_name, write_mode, engine=self.engine)
        
        if success:
            self.logger.info(f"数据已保存到 {table_name}，共 {len(data)} 条记录，写入模式: {write_mode}")
        else:
            self.logger.error(f"数据保存到 {table_name} 失败，写入模式: {write_mode}")