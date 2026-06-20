
# loader.py
import pandas as pd
import time
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime, timedelta
import sys
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from data.config.database import text, engine, upsert_data, save_to_database
from data.etl.extractor import fetch_data_with_pagination, fetch_data_by_stock_batches
from data.etl.validator import validate_df
from data.etl.transformer import transform_data, _get_table_name
from data.utils.date_utils import get_today_str, get_recent_weekday, get_recent_month, get_recent_quarter_dates, get_month_start_end


# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def process_api_params(api_name: str, params: Dict, bizdate: Optional[str] = None) -> List[Dict]:
    """
    根据API名称处理参数，支持手动指定业务日期
    
    Args:
        api_name: API名称
        params: 查询参数
        bizdate: 业务日期 (yyyymmdd)，如果为None则使用默认逻辑
        
    Returns:
        处理后的参数列表
    """
    # 需要更新trade_date的API
    trade_date_apis = ['stock_st', 'suspend_d', 'daily', 'adj_factor', 'daily_basic', 'moneyflow', 'sw_daily', 'weekly', 'monthly']
    
    # 需要遍历财报最近若干个季度的API
    quarterly_apis = ['income_vip', 'balancesheet_vip', 'cashflow_vip', 'disclosure_date']
    
    # 需要更新trade_date且遍历指数代码的API
    index_daily_apis = ['index_daily', 'index_dailybasic']
    
    # 需要处理月份开始和结束日期且遍历指数代码的API
    index_weight_apis = ['index_weight']
    
    # 需要遍历list_status的API
    stock_basic_apis = ['stock_basic']
    
    # 需要遍历is_new的API
    index_member_apis = ['index_member_all']

    # 关心的指数
    index_codes = ['000300.SH', '000852.SH', '000905.SH', '000906.SH', 
                  '000922.CSI', '000985.CSI', '399300.SZ', '399852.SZ', 
                  '399905.SZ', '930955.CSI', '932000.CSI']
    
    if api_name in trade_date_apis:
        # 对于周线和月线API，用对应的函数处理bizdate，计算最近一个交易的工作日或者月末
        if api_name == 'weekly':
            params['trade_date'] = get_recent_weekday(date_str=bizdate) if bizdate else get_recent_weekday()
        elif api_name == 'monthly':
            params['trade_date'] = get_recent_month(date_str=bizdate) if bizdate else get_recent_month()
        else:
            # 其他API直接使用bizdate或当天日期
            if bizdate:
                params['trade_date'] = bizdate
            else:
                params['trade_date'] = get_today_str()
        return [params]
    
    elif api_name in quarterly_apis:
        # 如果有指定业务日期，基于指定日期获取最近若干个季度；否则使用最近季度
        if bizdate:
            quarter_dates = get_recent_quarter_dates(date_str=bizdate)
        else:
            quarter_dates = get_recent_quarter_dates()
            
        param_list = []
        for period in quarter_dates:
            new_params = params.copy()
            new_params['period' if api_name != 'disclosure_date' else 'end_date'] = period
            param_list.append(new_params)
        return param_list
    
    elif api_name in index_daily_apis:
        # 指数日线API也支持指定业务日期
        if bizdate:
            params['trade_date'] = bizdate
        else:
            params['trade_date'] = get_today_str()
            
        param_list = []
        for ts_code in index_codes:
            new_params = params.copy()
            new_params['ts_code'] = ts_code
            param_list.append(new_params)
        return param_list
    
    elif api_name in index_weight_apis:
        # index_weight API需要处理月份的开始和结束日期
        # 从上月1号到今天，理由：有可能每天都更新（已经不多），也有可能一个月过完后才更新，两者都包括
        start_date = (datetime.strptime(bizdate, '%Y%m%d').replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y%m%d')
        end_date = bizdate
        
        param_list = []
        for index_code in index_codes:
            new_params = params.copy()
            new_params['index_code'] = index_code
            new_params['start_date'] = start_date
            new_params['end_date'] = end_date
            param_list.append(new_params)
        
        return param_list
    
    elif api_name in stock_basic_apis:
        # stock_basic API需要遍历list_status为L和D
        param_list = []
        for list_status in ['L', 'D']:
            new_params = params.copy()
            new_params['list_status'] = list_status
            param_list.append(new_params)
        
        return param_list
    
    elif api_name in index_member_apis:
        # index_member_all API需要遍历is_new为Y和N
        param_list = []
        for is_new in ['Y', 'N']:
            new_params = params.copy()
            new_params['is_new'] = is_new
            param_list.append(new_params)
        
        return param_list
    
    else:
        return [params]


def history_backfill(api_name: str, params: Dict, 
                    write_mode: str = 'append',
                    process_params: bool = False,
                    bizdate: Optional[str] = None) -> bool:
    """
    历史数据回补（生产模式），支持指定业务日期
    
    Args:
        api_name: API名称
        params: 查询参数
        write_mode: 写入模式 ('truncate', 'upsert', 'append')
        process_params: 是否处理参数（默认False，直接使用传入的参数）
        bizdate: 业务日期 (yyyymmdd)，如果提供则覆盖默认日期逻辑
        
    Returns:
        bool: 是否成功
    """
    logger.info(f"开始回补 {api_name} 历史数据 (处理参数: {process_params}, 业务日期: {bizdate or '默认'})")
    
    try:
        # 处理参数（根据process_params决定）
        if process_params:
            processed_params_list = process_api_params(api_name, params, bizdate)
        else:
            processed_params_list = [params]  # 直接使用传入的参数
        
        all_dataframes = []
        
        for processed_params in processed_params_list:
            # 1. 抽取数据
            df = fetch_data_with_pagination(api_name, processed_params)
            if df is None or df.empty:
                logger.warning(f"{api_name} 参数集无数据: {processed_params}")
                continue
            
            # 2. 验证数据
            validation = validate_df(df, api_name)
            if validation.get('is_empty', True):
                logger.warning(f"{api_name} 数据为空: {processed_params}")
                continue
            
            # 3. 转换数据
            df_transformed = transform_data(df, api_name)
            all_dataframes.append(df_transformed)
        
        if not all_dataframes:
            logger.error(f"{api_name} 所有参数集均无数据")
            return False
        
        # 合并所有数据框
        final_df = pd.concat(all_dataframes, ignore_index=True)
        
        # 4. 保存到数据库
        table_name = _get_table_name(api_name)
        success = save_to_database(final_df, table_name, write_mode, engine=engine)
        
        if success:
            logger.info(f"{api_name} 回补完成: {len(final_df)} 条记录")
        else:
            logger.error(f"{api_name} 回补失败")
        
        return success
        
    except Exception as e:
        logger.error(f"{api_name} 回补异常: {e}")
        return False


def history_backfill_debug(api_name: str, params: Dict, 
                          write_mode: str = 'append',
                          auto_save: bool = False,
                          process_params: bool = False,
                          bizdate: Optional[str] = None) -> Tuple[bool, Dict, Optional[pd.DataFrame]]:
    """
    历史数据回补（调试模式），支持指定业务日期
    
    Args:
        api_name: API名称
        params: 查询参数
        write_mode: 写入模式 ('truncate', 'upsert', 'append')
        auto_save: 是否自动保存到数据库
        process_params: 是否处理参数（默认False，直接使用传入的参数）
        bizdate: 业务日期 (yyyymmdd)，如果提供则覆盖默认日期逻辑
        
    Returns:
        Tuple[bool, Dict, Optional[pd.DataFrame]]: 
            (是否成功, 调试信息, 转换后的数据框)
    """
    debug_info = {
        'api_name': api_name, 
        'steps': {}, 
        'error': None, 
        'saved': False,
        'process_params': process_params,
        'bizdate': bizdate
    }
    
    try:
        # 处理参数（根据process_params决定）
        if process_params:
            processed_params_list = process_api_params(api_name, params, bizdate)
        else:
            processed_params_list = [params]  # 直接使用传入的参数
        
        all_dataframes = []
        debug_info['param_sets'] = len(processed_params_list)
        
        for i, processed_params in enumerate(processed_params_list):
            # 1. 抽取数据
            df = fetch_data_with_pagination(api_name, processed_params)
            if df is None or df.empty:
                logger.warning(f"参数集 {i+1} 无数据")
                continue
            
            # 2. 验证数据
            validation = validate_df(df, api_name)
            if validation.get('is_empty', True):
                logger.warning(f"参数集 {i+1} 数据为空")
                continue
            
            # 3. 转换数据
            df_transformed = transform_data(df, api_name)
            all_dataframes.append(df_transformed)
            debug_info[f'param_set_{i+1}'] = {'params': processed_params, 'rows': len(df_transformed)}
        
        if not all_dataframes:
            debug_info['error'] = f"{api_name} 所有参数集均无数据"
            return False, debug_info, None
        
        # 合并所有数据框
        final_df = pd.concat(all_dataframes, ignore_index=True)
        
        # 4. 获取表名
        table_name = _get_table_name(api_name)
        debug_info['table_name'] = table_name
        debug_info['write_mode'] = write_mode
        debug_info['total_rows'] = len(final_df)
        
        # 5. 自动保存（如果启用）
        if auto_save:
            save_success = save_to_database(final_df, table_name, write_mode, engine=engine)
            debug_info['saved'] = save_success
            if save_success:
                logger.info(f"自动保存成功: {table_name} ({len(final_df)} 行)")
        
        logger.info(f"调试完成: {api_name}, 参数集: {len(processed_params_list)}, 总行数: {len(final_df)}")
        return True, debug_info, final_df
        
    except Exception as e:
        debug_info['error'] = f"{api_name} 回补异常: {e}"
        return False, debug_info, None


def history_backfill_by_date_range(api_name: str, params: Dict, 
                                 start_date: str, end_date: str,
                                 batch_days: int = 30,
                                 write_mode: str = 'append',
                                 process_params: bool = False,
                                 bizdate: Optional[str] = None) -> bool:
    """
    按日期范围回补历史数据（生产模式，直接写入数据库），支持指定业务日期
    
    Args:
        api_name: API名称
        params: 原始查询参数（会被更新start_date和end_date）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        batch_days: 分批处理的天数
        write_mode: 写入模式 ('truncate', 'upsert', 'append')
        process_params: 是否处理参数（默认False，直接使用传入的参数）
        bizdate: 业务日期 (yyyymmdd)，如果提供则覆盖默认日期逻辑
        
    Returns:
        bool: 是否成功
    """
    logger.info(f"开始日期范围回补 {api_name}: {start_date} 到 {end_date} "
                f"(批次天数: {batch_days}, 模式: {write_mode}, "
                f"处理参数: {process_params}, 业务日期: {bizdate or '默认'})")
    
    # 验证日期格式
    try:
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
    except ValueError as e:
        logger.error(f"日期格式错误: {start_date} 或 {end_date}, 错误: {e}")
        return False
    
    if start_dt > end_dt:
        logger.error(f"开始日期 {start_date} 不能晚于结束日期 {end_date}")
        return False
    
    # 复制原始参数，避免修改原字典
    base_params = params.copy()
    
    current_start = start_dt
    success_count = 0
    total_batches = 0
    
    while current_start <= end_dt:
        # 计算当前批次的结束日期
        batch_end = min(current_start + timedelta(days=batch_days - 1), end_dt)
        
        # 构建当前批次的查询参数
        batch_params = base_params.copy()
        batch_params.update({
            'start_date': current_start.strftime('%Y%m%d'),
            'end_date': batch_end.strftime('%Y%m%d')
        })
        
        # 执行回补
        logger.info(f"处理批次 {total_batches + 1}: {current_start.strftime('%Y%m%d')} 到 {batch_end.strftime('%Y%m%d')}")
        success = history_backfill(api_name, batch_params, write_mode, process_params, bizdate)
        total_batches += 1
        
        if success:
            success_count += 1
            logger.info(f"✅ 批次 {total_batches} 完成: {current_start.strftime('%Y%m%d')} 到 {batch_end.strftime('%Y%m%d')}")
        else:
            logger.error(f"❌ 批次 {total_batches} 失败: {current_start.strftime('%Y%m%d')} 到 {batch_end.strftime('%Y%m%d')}")
        
        # 移动到下一批次
        current_start = batch_end + timedelta(days=1)
        
        # 批次间延迟，避免API限制
        time.sleep(1)
    
    success_rate = (success_count / total_batches) * 100 if total_batches > 0 else 0
    logger.info(f"日期范围回补完成: {success_count}/{total_batches} 批次成功 ({success_rate:.1f}%)")
    
    return success_count > 0


def run_single_api_backfill(api_name: str, api_config: Dict, 
                           debug_mode: bool = False, 
                           auto_save: bool = False,
                           process_params: bool = False,
                           bizdate: Optional[str] = None) -> Dict[str, Any]:
    """
    执行单个API的回补（通过api_name直接指定），支持指定业务日期
    
    Args:
        api_name: 要回补的API名称
        api_config: 完整的API配置字典
        debug_mode: 是否为调试模式
        auto_save: 是否自动保存到数据库
        process_params: 是否处理参数（默认False，直接使用传入的参数）
        bizdate: 业务日期 (yyyymmdd)，如果提供则覆盖默认日期逻辑
        
    Returns:
        Dict: 执行结果
    """
    # 在配置中查找对应的API配置
    config_key = None
    config = None
    
    # 遍历配置，查找匹配的api_name
    for key, cfg in api_config.items():
        if cfg.get('api_name') == api_name:
            config_key = key
            config = cfg
            break
    
    if config is None:
        error_msg = f"未找到API配置: {api_name}"
        logger.error(error_msg)
        return {'success': False, 'error': error_msg}
    
    params = config['params'].copy()
    write_mode = config.get('write_mode', 'append')
    
    logger.info(f"开始处理API: {api_name} (配置键: {config_key}), "
                f"调试模式: {debug_mode}, 自动保存: {auto_save}, "
                f"处理参数: {process_params}, 业务日期: {bizdate or '默认'}")
    
    try:
        if debug_mode:
            # 调试模式
            success, debug_info, df = history_backfill_debug(
                api_name=api_name,
                params=params,
                write_mode=write_mode,
                auto_save=auto_save,
                process_params=process_params,
                bizdate=bizdate
            )
            
            return {
                'success': success,
                'api_name': api_name,
                'config_key': config_key,
                'write_mode': write_mode,
                'debug_info': debug_info,
                'dataframe': df,
                'auto_save': auto_save,
                'process_params': process_params,
                'bizdate': bizdate
            }
        else:
            # 生产模式
            success = history_backfill(
                api_name=api_name,
                params=params,
                write_mode=write_mode,
                process_params=process_params,
                bizdate=bizdate
            )
            
            return {
                'success': success,
                'api_name': api_name,
                'config_key': config_key,
                'write_mode': write_mode,
                'process_params': process_params,
                'bizdate': bizdate
            }
        
    except Exception as e:
        logger.error(f"处理 {api_name} 时发生异常: {e}")
        return {
            'success': False,
            'error': str(e),
            'api_name': api_name,
            'config_key': config_key
        }


def run_batch_backfills_by_api_names(api_names: List[str], api_config: Dict,
                                    debug_mode: bool = False, 
                                    auto_save: bool = False,
                                    process_params: bool = False,
                                    bizdate: Optional[str] = None) -> Dict[str, Any]:
    """
    批量执行指定API名称列表的回补，支持指定业务日期
    
    Args:
        api_names: API名称列表
        api_config: 完整的API配置字典
        debug_mode: 是否为调试模式
        auto_save: 是否自动保存到数据库
        process_params: 是否处理参数（默认False，直接使用传入的参数）
        bizdate: 业务日期 (yyyymmdd)，如果提供则覆盖默认日期逻辑
        
    Returns:
        Dict: 执行结果
    """
    results = {}
    
    logger.info(f"开始批量回补API: {len(api_names)} 个API, "
                f"调试模式: {debug_mode}, 自动保存: {auto_save}, "
                f"处理参数: {process_params}, 业务日期: {bizdate or '默认'}")
    
    for api_name in api_names:
        logger.info(f"处理API: {api_name}")
        result = run_single_api_backfill(
            api_name=api_name, 
            api_config=api_config, 
            debug_mode=debug_mode, 
            auto_save=auto_save,
            process_params=process_params,
            bizdate=bizdate
        )
        results[api_name] = result
        
        if result.get('success'):
            logger.info(f"{api_name} 回补成功")
        else:
            error_msg = result.get('error', '未知错误')
            logger.error(f"{api_name} 回补失败: {error_msg}")
    
    # 统计成功和失败的数量
    success_count = sum(1 for result in results.values() if result.get('success'))
    total_count = len(results)
    
    logger.info(f"批量回补完成: {success_count}/{total_count} 个API成功")
    
    return results

def save_debug_data(debug_info: Dict[str, Any], df: pd.DataFrame, engine=engine) -> bool:
    """
    保存调试数据到数据库（简化版）
    
    Args:
        debug_info: 调试信息字典，包含table_name和write_mode
        df: 要保存的DataFrame
        engine: 数据库引擎
        
    Returns:
        bool: 保存是否成功
    """
    try:
        # 从debug_info中提取表名和写入模式
        table_name = debug_info.get('table_name')
        write_mode = debug_info.get('write_mode', 'append')  # 默认追加模式
        
        # 基本参数验证
        if not table_name:
            logger.error("debug_info中缺少table_name")
            return False
            
        if df is None or df.empty:
            logger.warning(f"DataFrame为空，跳过保存到表 {table_name}")
            return True  # 空数据不算错误
            
        if engine is None:
            logger.error("数据库引擎未提供")
            return False
        
        # 直接调用已有的save_to_database函数
        save_success = save_to_database(df=df,table_name=table_name,write_mode=write_mode,engine=engine)
        
        # 记录结果
        if save_success:
            logger.info(f"调试数据保存成功: {table_name}, 模式={write_mode}, 行数={len(df)}")
        else:
            logger.error(f"调试数据保存失败: {table_name}, 模式={write_mode}")
            
        return save_success
        
    except Exception as e:
        logger.error(f"保存调试数据时发生异常: {e}")
        return False      
            
def save_all_debug_data(results: Dict[str, Any]) -> Dict[str, bool]:
    """一键保存所有调试数据"""
    save_results = {}
    
    for api_name, result in results.items():
        if result.get('success') and result.get('dataframe') is not None:
            debug_info = result.get('debug_info', {})
            df = result['dataframe']
            save_success = save_debug_data(debug_info, df)
            save_results[api_name] = save_success
            
            if save_success:
                logger.info(f"{api_name} 保存成功")
            else:
                logger.error(f"{api_name} 保存失败")
        else:
            save_results[api_name] = False
            logger.warning(f"{api_name} 无数据可保存")
    
    return save_results