# validator.py
import pandas as pd
from typing import Dict, Any, Optional
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def validate_df(df: pd.DataFrame, 
               df_name: str = "数据",
               null_threshold: float = 0.5) -> Dict[str, Any]:
    """
    极简数据验证器
    
    Args:
        df: 要验证的DataFrame
        df_name: 数据名称
        null_threshold: 空值比例告警阈值(0-1)
    
    Returns:
        验证结果字典
    """
    # 基础检查
    if df is None or df.empty:
        logger.warning(f"{df_name} - 数据为空")
        return {'is_empty': True, 'warnings': ['数据为空']}
    
    result = {
        'is_empty': False,
        'row_count': len(df),
        'column_count': len(df.columns),
        'data_types': {},
        'null_ratios': {},
        'warnings': []
    }
    
    logger.info(f"{df_name}: {len(df)}行, {len(df.columns)}列")
    
    # 分析每列
    for col in df.columns:
        dtype = str(df[col].dtype)
        null_ratio = df[col].isnull().sum() / len(df)
        
        result['data_types'][col] = dtype
        result['null_ratios'][col] = round(null_ratio, 4)
        
        # 空值告警
        if null_ratio > null_threshold:
            warning = f"{col}: {null_ratio:.1%}为空"
            result['warnings'].append(warning)
            logger.warning(f"{df_name} - {warning}")
    
    # 输出摘要
    if result['warnings']:
        logger.warning(f"{df_name} - 发现{len(result['warnings'])}个问题")
    else:
        logger.info(f"{df_name} - 数据质量良好")
    
    return result

def quick_check(df: pd.DataFrame, df_name: str = "数据") -> None:
    """
    快速检查并打印摘要
    """
    result = validate_df(df, df_name)
    
    if result['is_empty']:
        print(f"❌ {df_name} - 数据为空")
        return
    
    print(f"📊 {df_name}: {result['row_count']}行 × {result['column_count']}列")
    
    # 显示高空值列
    high_null_cols = [(col, ratio) for col, ratio in result['null_ratios'].items() if ratio > 0.1]
    if high_null_cols:
        print("⚠️  高空值列:")
        for col, ratio in high_null_cols[:3]:  # 只显示前3个
            print(f"   {col}: {ratio:.1%}")
    
    if result['warnings']:
        print(f"🔔 警告: {len(result['warnings'])}个")