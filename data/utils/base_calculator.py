import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any, Callable
from sqlalchemy import create_engine, text
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from data.config.database import engine, upsert_data, save_to_database

def setup_logger(name: str = "DataProcessor") -> logging.Logger:
    """设置日志记录器"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def get_stock_list(engine=engine, logger: Optional[logging.Logger] = None) -> List[str]:
    """
    获取所有股票列表（最简单版本，无任何限制）
    
    Args:
        engine: 数据库引擎（默认使用全局engine）
        logger: 日志记录器
        
    Returns:
        股票代码列表
    """
    if logger is None:
        logger = setup_logger("StockList")
    
    query = "SELECT DISTINCT ts_code FROM stock_basic ORDER BY ts_code"
    
    logger.info("获取所有股票列表（无限制）")
    
    try:
        stock_df = pd.read_sql(query, engine)
        stock_list = stock_df['ts_code'].tolist()
        logger.info(f"获取到 {len(stock_list)} 只股票")
        return stock_list
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return []
    
def batch_process(
    get_data_func: Callable[[Optional[str], Optional[str], List[str], Any], pd.DataFrame],
    process_data_func: Callable[[pd.DataFrame, Optional[str], Optional[str], Any], pd.DataFrame],
    entity_list: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    batch_size: int = 100,
    auto_save: bool = False,
    save_func: Optional[Callable[[pd.DataFrame, str, str, Optional[str], Optional[str]], None]] = None,
    table_name: str = 'default_table',
    write_mode: str = 'append',
    logger: Optional[logging.Logger] = None,
    **kwargs
) -> pd.DataFrame:
    """
    通用批量处理函数（实体列表为必填，总是按批次处理）
    
    Args:
        get_data_func: 数据获取函数 (start_date, end_date, entity_list, **kwargs) -> DataFrame
        process_data_func: 数据处理函数 (data, start_date, end_date, **kwargs) -> DataFrame
        entity_list: 实体列表（如股票代码列表），必填
        start_date: 开始日期 (yyyymmdd)，可选
        end_date: 结束日期 (yyyymmdd)，可选
        batch_size: 每批处理的实体数量
        auto_save: 是否自动保存结果
        save_func: 保存函数，如果auto_save为True则必须提供
        table_name: 表名
        write_mode: 写入模式 ('truncate', 'upsert', 'append', 'overwrite')
        logger: 日志记录器
        **kwargs: 额外参数传递给数据获取和处理函数
        
    Returns:
        处理后的DataFrame
    """
    if logger is None:
        logger = setup_logger("BatchProcessor")
    
    if not entity_list:
        logger.error("entity_list不能为空")
        return pd.DataFrame()
    
    # 转换日期格式，永远是yyyymmdd
    start_date = start_date.replace('-','')
    end_date = end_date.replace('-','')
    
    logger.info(f"开始批量处理: 实体数={len(entity_list)}, "
                f"日期范围={start_date or '开始'}~{end_date or '结束'}, 批次大小={batch_size}")
    
    all_results = []
    total_batches = (len(entity_list) - 1) // batch_size + 1
    
    for i in range(0, len(entity_list), batch_size):
        batch_entities = entity_list[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        logger.info(f"处理批次 {batch_num}/{total_batches}, 实体数: {len(batch_entities)}")
        
        # 获取当前批次数据
        batch_data = get_data_func(start_date, end_date, batch_entities, **kwargs)
        if batch_data.empty:
            continue
        
        # 处理当前批次数据
        batch_result = process_data_func(batch_data, start_date, end_date, **kwargs)
        if not batch_result.empty:
            all_results.append(batch_result)
            
            # 如果启用自动保存，分批保存
            if auto_save and save_func:
                save_func(batch_result, table_name, write_mode, start_date, end_date)
                logger.info(f"批次 {batch_num} 结果已保存到 {table_name}")
    
    if all_results:
        final_result = pd.concat(all_results, ignore_index=True)
        logger.info(f"批量处理完成，总共处理 {len(final_result)} 条记录")
        return final_result
    else:
        logger.warning("批量处理完成，但没有有效结果")
        return pd.DataFrame()

def incremental_update(
    get_data_func: Callable[[Optional[str], Optional[str], Optional[List[str]], Any], pd.DataFrame],
    process_data_func: Callable[[pd.DataFrame, Optional[str], Optional[str], Any], pd.DataFrame],
    start_date: str,
    end_date: str,
    auto_save: bool = False,
    save_func: Optional[Callable[[pd.DataFrame, str, str, Optional[str], Optional[str]], None]] = None,
    table_name: str = 'default_table',
    write_mode: str = 'append',
    logger: Optional[logging.Logger] = None,
    **kwargs
) -> pd.DataFrame:
    """
    增量更新函数（处理全部实体指定日期范围的数据）
    
    Args:
        get_data_func: 数据获取函数
        process_data_func: 数据处理函数
        start_date: 开始日期 (yyyymmdd)，必填
        end_date: 结束日期 (yyyymmdd)，必填
        auto_save: 是否自动保存结果
        save_func: 保存函数
        table_name: 表名
        write_mode: 写入模式 ('truncate', 'upsert', 'append', 'overwrite')
        logger: 日志记录器
        **kwargs: 额外参数
        
    Returns:
        处理后的DataFrame
    """
    if logger is None:
        logger = setup_logger("IncrementalProcessor")
    
    logger.info(f"开始增量更新: {start_date} 到 {end_date}")
    
    # 获取增量数据（不指定实体列表，处理全部实体）
    # 转换日期格式，永远是yyyymmdd
    start_date = start_date.replace('-','')
    end_date = end_date.replace('-','')
    raw_data = get_data_func(start_date, end_date, None, **kwargs)
    
    if raw_data.empty:
        logger.warning(f"在 {start_date} 到 {end_date} 范围内没有找到数据")
        return pd.DataFrame()
    
    # 处理数据
    result = process_data_func(raw_data, start_date, end_date, **kwargs)
    logger.info(f"增量更新完成，处理 {len(result)} 条记录")
    
    # 自动保存
    if auto_save and save_func and not result.empty:
        save_func(result, table_name, write_mode, start_date, end_date)
        logger.info(f"结果已自动保存到 {table_name}")
    
    return result

class BaseCalculator:
    """数据计算器基类，提供批量处理和增量更新的通用方法"""
    
    def __init__(self, calculator_name: str, engine=None):
        """
        初始化计算器
        
        Args:
            calculator_name: 计算器名称
            engine: 数据库引擎，如果为None则使用全局engine
        """
        # 使用指定的engine或全局engine
        self.engine = engine if engine is not None else engine
        self.calculator_name = calculator_name
        self.logger = self._setup_logging()
        
        # 设置默认写入模式
        self.default_table_name = 'default_table'
        self.default_write_mode = 'append'
        
        self.logger.info(f"{calculator_name} 初始化完成，使用引擎: {self.engine.url if hasattr(self.engine, 'url') else '自定义引擎'}")
    
    def _setup_logging(self) -> logging.Logger:
        """设置日志"""
        logger = logging.getLogger(self.calculator_name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def get_stock_list(self) -> List[str]:
        """获取股票列表（使用通用函数）"""
        return get_stock_list(self.engine, self.logger)
    
    def batch_process(
        self,
        entity_list: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        batch_size: int = 100,
        auto_save: bool = False,
        table_name: str = None,
        write_mode: str = None,
        **kwargs
    ) -> pd.DataFrame:
        """批量处理（使用通用函数）"""
        # 使用默认值
        table_name = table_name or self.default_table_name
        write_mode = write_mode or self.default_write_mode
        
        return batch_process(
            get_data_func=self.get_data,
            process_data_func=self.process_data,
            entity_list=entity_list,
            start_date=start_date,
            end_date=end_date,
            batch_size=batch_size,
            auto_save=auto_save,
            save_func=self.save_to_database,
            table_name=table_name,
            write_mode=write_mode,
            logger=self.logger,
            **kwargs
        )
    
    def incremental_update(
        self,
        start_date: str,
        end_date: str,
        auto_save: bool = False,
        table_name: str = None,
        write_mode: str = None,
        **kwargs
    ) -> pd.DataFrame:
        """增量更新（使用通用函数）"""
        # 使用默认值
        table_name = table_name or self.default_table_name
        write_mode = write_mode or self.default_write_mode
        
        return incremental_update(
            get_data_func=self.get_data,
            process_data_func=self.process_data,
            start_date=start_date,
            end_date=end_date,
            auto_save=auto_save,
            save_func=self.save_to_database,
            table_name=table_name,
            write_mode=write_mode,
            logger=self.logger,
            **kwargs
        )
    
    def save_to_database(
        self, 
        data: pd.DataFrame, 
        table_name: str = None, 
        write_mode: str = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> None:
        """
        保存数据到数据库（支持overwrite模式）
        """
        # 使用默认值
        table_name = table_name or self.default_table_name
        write_mode = write_mode or self.default_write_mode
        
        # 处理overwrite模式
        if write_mode == 'overwrite':
            if start_date is None or end_date is None:
                raise ValueError("overwrite模式必须提供start_date和end_date参数")
            
            # 转换日期格式，永远是yyyymmdd
            start_date = start_date.replace('-','')
            end_date = end_date.replace('-','')
            
            # 先删除指定日期范围内的数据
            try:
                # 使用text()包装SQL语句
                delete_sql = text(f"""
                    DELETE FROM {table_name} 
                    WHERE trade_date BETWEEN :start_date AND :end_date
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
                raise
            
            # 然后使用append模式插入新数据
            write_mode = 'append'
        
        # 使用database.py中的save_to_database函数
        success = save_to_database(data, table_name, write_mode, engine=self.engine)
        
        if success:
            self.logger.info(f"数据已保存到 {table_name}，共 {len(data)} 条记录，写入模式: {write_mode}")
        else:
            self.logger.error(f"数据保存到 {table_name} 失败，写入模式: {write_mode}")
    
    def get_data(self, start_date: Optional[str], end_date: Optional[str], entity_list: Optional[List[str]], **kwargs) -> pd.DataFrame:
        """获取数据（子类必须实现）"""
        raise NotImplementedError("子类必须实现get_data方法")
    
    def process_data(self, data: pd.DataFrame, start_date: Optional[str], end_date: Optional[str], **kwargs) -> pd.DataFrame:
        """处理数据（子类必须实现）"""
        raise NotImplementedError("子类必须实现process_data方法")