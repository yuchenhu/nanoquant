# transformer.py
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
import logging
import sys
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from data.config.api import API_CONFIG
from data.config.database import get_table_schema

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def transform_data(df: pd.DataFrame, api_name: str) -> pd.DataFrame:
    """
    极简数据转换器：将DataFrame按照规定的schema进行数据类型转换
    
    Args:
        df: API返回的原始数据
        api_name: API名称
    
    Returns:
        转换后的DataFrame
    """
    # 检查输入数据
    if df is None or df.empty:
        logger.warning(f"{api_name} 数据为空，跳过转换")
        return df
    
    # 获取API对应的表名
    table_name = _get_table_name(api_name)
    if not table_name:
        logger.info(f"{api_name} 无对应表名，返回原始数据")
        return df
    
    # 获取表结构
    schema = get_table_schema(table_name)
    if not schema:
        logger.info(f"{table_name} 表结构不存在，返回原始数据")
        return df
    
    # 执行转换
    return _apply_schema(df, schema, api_name)

def _get_table_name(api_name: str) -> Optional[str]:
    """获取API对应的表名"""
    if api_name in API_CONFIG and 'table_name' in API_CONFIG[api_name]:
        return API_CONFIG[api_name]['table_name']
    return None

def _apply_schema(df: pd.DataFrame, schema: Dict[str, str], api_name: str) -> pd.DataFrame:
    """应用表结构转换"""
    
    result = df.copy()
    transformed_cols = 0
    
    for col, target_type in schema.items():
        if col in result.columns:
            try:
                result[col] = _convert_type(result[col], target_type)
                transformed_cols += 1
            except Exception as e:
                logger.warning(f"{api_name}.{col} 转换失败: {e}")
    
    if transformed_cols > 0:
        logger.info(f"{api_name} 转换了 {transformed_cols} 列")
    
    ##将nan和nat转换为none（mysql兼容）
    return convert_nan_nat_to_none(result)

def _convert_type(series: pd.Series, target_type: str) -> pd.Series:
    """转换数据类型"""
    if target_type == 'string':
        return series.astype(str)
    elif target_type == 'int':
        return pd.to_numeric(series, errors='coerce').astype(int)
    elif target_type == 'float':
        return pd.to_numeric(series, errors='coerce')
    elif target_type == 'date':
        return pd.to_datetime(series, errors='coerce')
    elif target_type == 'bool':
        return series.astype(bool)
    else:
        return series  # 未知类型保持原样

def convert_nan_nat_to_none(df):
    """
    将DataFrame中的NaN和NaT值转换为None，因为MySQL不接受Nan/Nat
    适用于pandas 2.3.0+
    """
    if df is None or df.empty:
        return df
        
    # 在新版本pandas中，这个方法应该是可靠的
    result = df.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
    
    return result

def _is_date_only(timestamp):
    """检查时间戳是否只有日期部分（时间为 00:00:00）"""
    try:
        if hasattr(timestamp, 'time'):
            return timestamp.time() == pd.Timestamp('1970-01-01').time()
        return False
    except:
        return False

# 使用示例
if __name__ == "__main__":
    # 测试数据
    sample_data = pd.DataFrame({
        'ts_code': ['000001.SZ', '000002.SZ'],
        'trade_date': ['20230101', '20230102'],
        'open': ['10.5', '20.3'],
        'close': ['10.8', '20.9'],
        'vol': ['1000000', '2000000']
    })
    
    print("原始数据:")
    print(sample_data.dtypes)
    print(sample_data)
    
    # 测试转换
    transformed = transform_data(sample_data, 'daily')
    
    print("\n转换后数据:")
    print(transformed.dtypes)
    print(transformed)