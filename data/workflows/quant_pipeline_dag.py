"""
极简版量化数据流水线 - Apache Airflow
将所有内容放在一个文件中
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
import pandas as pd
import time
from datetime import datetime, timedelta
import logging
import sys
import os

project_root = os.path.dirname(os.path.dirname(os.getcwd()))
sys.path.append(project_root)

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(project_root)

from data.config.api import API_CONFIG
from data.config.database import *
from data.utils.date_utils import *
from data.utils.base_calculator import *
from data.etl.loader import *
from data.sql.stock_daily_wide import StockDailyWideCalculator
from data.sql.stock_percentiles import StockPercentilesCalculator
from data.sql.market_sentiment_daily import MarketSentimentDailyCalculator
from data.sql.market_sentiment_monthly import MarketSentimentMonthlyCalculator
from data.sql.mv_monthly import MvMonthlyCalculator
from data.label.forward_returns import ForwardReturnsCalculator

# 默认参数
default_args = {
    'owner': 'quant',
    'depends_on_past': False,
    'start_date': days_ago(1),
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

def task_fetch_market_data(bizdate=None, **context):
    """获取市场数据"""    
    if bizdate is None:
        bizdate = get_today_str()
    # 检查是否为交易日
    if not is_trading_day(bizdate):
        return {"success": True, "skipped": True, "message": f"跳过非交易日: {bizdate}"}
    
    print(f"获取市场数据: {bizdate}")
    
    DAILY_APIS = ['stock_basic','stock_st','suspend_d','daily','adj_factor','daily_basic','moneyflow','index_weight','weekly','monthly']
    
    result_dict = run_batch_backfills_by_api_names(
        api_config=API_CONFIG, 
        api_names=DAILY_APIS, 
        debug_mode=False, 
        auto_save=True, 
        bizdate=bizdate, 
        process_params=True
    )
    
    success = all([v['success'] for k, v in result_dict.items()])
    return {
        "success": success,
        "data": result_dict,
        "message": f"市场数据获取完成: {success}",
        "bizdate": bizdate
    }

def task_fetch_financial_data(bizdate=None, **context):
    """获取财务数据"""

    if bizdate is None:
        bizdate = get_today_str()
    
    # 检查是否为财报披露月份
    month = datetime.strptime(bizdate, '%Y%m%d').month
    if month not in [3, 4, 8, 10]:
        return {"success": True, "skipped": True, "message": f"跳过非财报月: {bizdate}"}
    
    print(f"获取财务数据: {bizdate}")
    
    FINANCIAL_APIS = ['income_vip','balancesheet_vip','cashflow_vip','disclosure_date']
    
    result_dict = run_batch_backfills_by_api_names(
        api_config=API_CONFIG, 
        api_names=FINANCIAL_APIS, 
        debug_mode=False, 
        auto_save=True, 
        bizdate=bizdate, 
        process_params=True
    )
    
    success = all([v['success'] for k, v in result_dict.items()])
    return {
        "success": success,
        "data": result_dict,
        "message": f"财务数据获取完成: {success}",
        "bizdate": bizdate
    }

def task_fetch_index_data(bizdate=None, **context):
    """获取指数数据"""    
    if bizdate is None:
        bizdate = get_today_str()
    # 检查是否为交易日
    if not is_trading_day(bizdate):
        return {"success": True, "skipped": True, "message": f"跳过非交易日: {bizdate}"}
    
    print(f"获取指数数据: {bizdate}")
    
    INDEX_APIS = ['index_basic','index_daily','index_dailybasic','index_classify','index_member_all','sw_daily']
    
    result_dict = run_batch_backfills_by_api_names(
        api_config=API_CONFIG, 
        api_names=INDEX_APIS, 
        debug_mode=False, 
        auto_save=True, 
        bizdate=bizdate, 
        process_params=True
    )
    
    success = all([v['success'] for k, v in result_dict.items()])
    return {
        "success": success,
        "data": result_dict,
        "message": f"指数数据获取完成: {success}",
        "bizdate": bizdate
    }
    
def task_calculate_daily_wide(bizdate=None, **context):
    """计算日行情宽表"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"计算日行情宽表: {bizdate}")
    
    try:
        stock_daily_wide = StockDailyWideCalculator()
        result_df = stock_daily_wide.incremental_update(start_date=bizdate, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"日行情宽表计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"日行情宽表计算失败: {e}",
            "bizdate": bizdate
        }

def task_calculate_mv_monthly(bizdate=None, **context):
    """计算月度市值"""
    
    if bizdate is None:
        bizdate = get_today_str()

    monthly_first = bizdate[:6]+'01'
    
    print(f"计算月度市值: {bizdate}")
    
    try:
        mv_monthly = MvMonthlyCalculator()
        result_df = mv_monthly.incremental_update(start_date=monthly_first, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"月度市值计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"月度市值计算失败: {e}",
            "bizdate": bizdate
        }

def task_calculate_stock_percentiles(bizdate=None, **context):
    """计算历史百分位"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"计算历史百分位: {bizdate}")
    
    try:
        stock_percentiles = StockPercentilesCalculator()
        result_df = stock_percentiles.incremental_update(start_date=bizdate, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"历史百分位计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"历史百分位计算失败: {e}",
            "bizdate": bizdate
        }

def task_calculate_market_sentiment_daily(bizdate=None, **context):
    """计算每日市场热度"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"计算每日市场热度: {bizdate}")
    
    try:
        market_sentiment_daily = MarketSentimentDailyCalculator()
        result_df = market_sentiment_daily.incremental_update(start_date=bizdate, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"每日市场热度计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"每日市场热度计算失败: {e}",
            "bizdate": bizdate
        }

def task_calculate_market_sentiment_monthly(bizdate=None, **context):
    """计算按月汇总市场热度"""
    
    if bizdate is None:
        bizdate = get_today_str()
        
    d = datetime.strptime(bizdate, '%Y%m%d')
    last_month_first = (datetime(d.year, d.month, 1) - timedelta(days=1)).strftime('%Y%m') + '01' ##把上月数据完整更新+本月截至当天
    
    print(f"计算按月汇总市场热度: {bizdate}")
    
    try:
        market_sentiment_monthly = MarketSentimentMonthlyCalculator()
        result_df = market_sentiment_monthly.incremental_update(start_date=last_month_first, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"按月汇总市场热度计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"按月汇总市场热度计算失败: {e}",
            "bizdate": bizdate
        }

def task_calculate_forward_returns(bizdate=None, **context):
    """计算未来收益率"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"计算未来收益率: {bizdate}")
    
    try:
        forward_returns = ForwardReturnsCalculator()
        start_date = get_previous_n_trading_date(bizdate, 250) #虽然持仓收益率目前最多算60天，但是是基于个股交易日计算的，因此要多回补一点，覆盖大多数个股停牌复牌
        result_df = forward_returns.incremental_update(start_date=start_date, end_date=bizdate, auto_save=True)
        
        success = len(result_df) > 0 if result_df is not None else False
        return {
            "success": success,
            "data": result_df,
            "message": f"未来收益率计算完成: {success}",
            "bizdate": bizdate
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"未来收益率计算失败: {e}",
            "bizdate": bizdate
        }

# 创建DAG
with DAG(
    'quant_pipeline_simple',
    default_args=default_args,
    description='量化数据流水线',
    schedule_interval='0 19 * * 1-5',  # 工作日19点执行
    catchup=False,
    tags=['quant'],
) as dag:
    
    # 获取业务日期的函数
    def get_bizdate(**context):
        from data.utils.date_utils import get_today_str, is_trading_day, get_previous_n_trading_date
        execution_date = context['execution_date']
        bizdate = execution_date.strftime('%Y%m%d')
        
        # 如果不是交易日，使用上一个交易日
        if not is_trading_day(bizdate):
            bizdate = get_previous_n_trading_date(bizdate, 1)
        
        return bizdate
    
    # 任务0: 获取业务日期
    get_bizdate_task = PythonOperator(
        task_id='get_business_date',
        python_callable=get_bizdate,
        provide_context=True,
    )
    
    # 任务1.1: 获取市场数据（依赖业务日期）
    fetch_market_data = PythonOperator(
        task_id='fetch_market_data',
        python_callable=task_fetch_market_data,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务1.2: 获取财务数据（并行执行）
    fetch_financial_data = PythonOperator(
        task_id='fetch_financial_data',
        python_callable=task_fetch_financial_data,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务1.3: 获取指数数据（并行执行）
    fetch_index_data = PythonOperator(
        task_id='fetch_index_data',
        python_callable=task_fetch_index_data,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务2.1: 计算日行情宽表（依赖市场数据）
    calculate_daily_wide = PythonOperator(
        task_id='calculate_daily_wide',
        python_callable=task_calculate_daily_wide,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )

    # 任务2.2: 计算月度市值（依赖市场数据）
    calculate_mv_monthly = PythonOperator(
        task_id='calculate_mv_monthly',
        python_callable=task_calculate_mv_monthly,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务3.1: 计算未来收益率（依赖日行情宽表）
    calculate_forward_returns = PythonOperator(
        task_id='calculate_forward_returns',
        python_callable=task_calculate_forward_returns,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务3.2: 计算历史百分位（依赖日行情宽表）
    calculate_stock_percentiles = PythonOperator(
        task_id='calculate_stock_percentiles',
        python_callable=task_calculate_stock_percentiles,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务4.1: 计算每日市场热度（依赖历史百分位）
    calculate_market_sentiment_daily = PythonOperator(
        task_id='calculate_market_sentiment_daily',
        python_callable=task_calculate_market_sentiment_daily,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 任务4.2: 计算按月汇总市场热度（依赖历史百分位）
    calculate_market_sentiment_monthly = PythonOperator(
        task_id='calculate_market_sentiment_monthly',
        python_callable=task_calculate_market_sentiment_monthly,
        op_kwargs={'bizdate': "{{ task_instance.xcom_pull(task_ids='get_business_date') }}"},
        provide_context=True,
    )
    
    # 汇总任务
    def pipeline_summary(**context):
        """流水线执行汇总"""
        bizdate = context['task_instance'].xcom_pull(task_ids='get_business_date')
        execution_date = context['execution_date']
        
        # 收集所有任务结果
        task_results = {}
        task_ids = [
            'fetch_market_data', 'fetch_financial_data', 'fetch_index_data',
            'calculate_daily_wide', 'calculate_mv_monthly', 
            'calculate_forward_returns', 'calculate_stock_percentiles', 
            'calculate_market_sentiment_daily', 'calculate_market_sentiment_monthly'
        ]
        
        for task_id in task_ids:
            result = context['task_instance'].xcom_pull(task_ids=task_id)
            task_results[task_id] = result
        
        success_count = sum(1 for r in task_results.values() if r and r.get('success'))
        total_count = len(task_results)
        
        print("=" * 60)
        print("量化流水线执行汇总")
        print("=" * 60)
        print(f"执行业务日期: {bizdate}")
        print(f"执行时间: {execution_date}")
        print(f"任务完成: {success_count}/{total_count}")
        
        for task_id, result in task_results.items():
            if result:
                status = "✅" if result.get('success') else "❌"
                skipped = "⏭️" if result.get('skipped') else ""
                message = result.get('message', 'N/A')
                print(f"{status} {task_id}: {message} {skipped}")
            else:
                print(f"❓ {task_id}: 无执行结果")
        
        print("=" * 60)
        
        # 判断整体是否成功
        overall_success = success_count == total_count
        if overall_success:
            print("流水线执行成功！")
        else:
            print("流水线执行存在失败任务，请检查日志")
        
        print("=" * 60)
        
        return {"success": overall_success, "bizdate": bizdate, "completed_tasks": success_count, "total_tasks": total_count}
    
    summary_task = PythonOperator(
        task_id='pipeline_summary',
        python_callable=pipeline_summary,
        provide_context=True,
    )
    
    # 设置依赖关系
    get_bizdate_task >> [fetch_market_data, fetch_financial_data, fetch_index_data]
    fetch_market_data >> [calculate_daily_wide, calculate_mv_monthly]
    calculate_daily_wide >> [calculate_forward_returns, calculate_stock_percentiles]
    calculate_stock_percentiles >> [calculate_market_sentiment_daily, calculate_market_sentiment_monthly]
    
    # 所有任务完成后执行汇总
    [fetch_financial_data, fetch_index_data, calculate_mv_monthly,
     calculate_forward_returns, calculate_market_sentiment_daily, calculate_market_sentiment_monthly] >> summary_task