# extractor.py
import tushare as ts
import pandas as pd
import time
import sys
import os
from typing import List, Dict, Any, Optional, Union
import logging


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# 导入配置
from data.config.api import TUSHARE_TOKEN, API_CONFIG

# 初始化tushare
pro = ts.pro_api(TUSHARE_TOKEN)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def split_ts_codes(ts_codes: List[str], batch_size: int = 100) -> List[List[str]]:
    """
    将股票代码列表分割成指定大小的批次
    """
    return [ts_codes[i:i + batch_size] for i in range(0, len(ts_codes), batch_size)]


def fetch_data_with_pagination(api_name: str, params: Dict, max_retries: int = 3) -> pd.DataFrame:
    """
    分页循环查询数据
    """
    all_data = []
    offset = 0
    retry_count = 0
    limit = API_CONFIG.get(api_name, {}).get('params', {}).get('limit', 5000)
    
    logger.info(f"开始分页查询 {api_name}, 每页限制: {limit}")
    
    while True:
        try:
            # 添加分页参数
            page_params = params.copy()
            page_params.update({'offset': offset})
            
            df = getattr(pro, api_name)(**page_params)
            
            if df is None or df.empty:
                break
                
            all_data.append(df)
            logger.info(f"已获取第 {offset//limit + 1} 页数据, 本页 {len(df)} 条记录")
            
            # 如果返回数据少于limit，说明已到最后一页
            if len(df) < limit:
                break
                
            offset += limit
            retry_count = 0  # 重置重试计数
            
            # 添加延迟避免频繁请求
            time.sleep(0.2)
            
        except Exception as e:
            logger.warning(f"第 {offset//limit + 1} 页查询失败: {str(e)}")
            retry_count += 1
            
            if retry_count >= max_retries:
                logger.error(f"达到最大重试次数，停止查询")
                break
                
            logger.info(f"等待 {retry_count * 2} 秒后重试...")
            time.sleep(retry_count * 2)
    
    if all_data:
        result = pd.concat(all_data, ignore_index=True)
        logger.info(f"查询完成，共获取 {len(result)} 条记录")
        return result
    
    logger.info("未获取到数据")
    return pd.DataFrame()

def fetch_data_by_stock_batches(api_name: str, ts_codes: List[str], 
                               base_params: Dict, batch_size: int = 100) -> pd.DataFrame:
    """
    分股票分页循环查询数据
    """
    ts_code_batches = split_ts_codes(ts_codes, batch_size)
    
    logger.info(f"开始分批获取 {api_name} 数据，共 {len(ts_codes)} 支股票，分 {len(ts_code_batches)} 批处理")
    
    all_data = []
    for i, batch in enumerate(ts_code_batches, 1):
        logger.info(f"处理第 {i}/{len(ts_code_batches)} 批，本批包含 {len(batch)} 支股票")
        
        batch_params = base_params.copy()
        batch_params['ts_code'] = ','.join(batch)
        
        batch_data = fetch_data_with_pagination(api_name, batch_params)
        
        if not batch_data.empty:
            all_data.append(batch_data)
        
        # 批次间延迟
        time.sleep(0.1)
    
    if all_data:
        result = pd.concat(all_data, ignore_index=True)
        logger.info(f"批次查询完成，共获取 {len(result)} 条记录")
        return result
    
    return pd.DataFrame()

def get_daily_data(trade_date: str = None, start_date: str = None, end_date: str = None,
                  ts_codes: List[str] = None, batch_size: int = 100) -> pd.DataFrame:
    """
    获取日线数据（示例用法）
    """
    params = {}
    
    # 日期参数处理
    if trade_date:
        params['trade_date'] = trade_date
    elif start_date and end_date:
        params['start_date'] = start_date
        params['end_date'] = end_date
    
    # 股票代码处理
    if ts_codes:
        if len(ts_codes) <= batch_size:
            params['ts_code'] = ','.join(ts_codes)
            return fetch_data_with_pagination('daily', params)
        else:
            return fetch_data_by_stock_batches('daily', ts_codes, params, batch_size)
    else:
        return fetch_data_with_pagination('daily', params)