"""
手动执行量化流水线（支持并行执行独立任务）
"""
import argparse
from datetime import datetime, timedelta
import sys
import os
import io
import codecs
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(project_root)
from data.utils.date_utils import get_today_str, is_trading_day, get_previous_n_trading_date

# 设置标准输出的编码为UTF-8
if sys.platform == "win32":
    # Windows系统特殊处理
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    else:
        # 对于旧版Python
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='ignore')
    
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    else:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='ignore')

        
def execute_task_async(task_func, bizdate, task_name, results, timeout=300):
    """异步执行单个任务"""
    try:
        print(f"开始执行: {task_name}")
        start_time = datetime.now()
        
        result = task_func(bizdate=bizdate)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        result['execution_duration'] = duration
        results[task_name] = result
        
        status = "✅" if result.get('success') else "❌"
        skipped = "⏭️" if result.get('skipped') else ""
        print(f"{status} 完成: {task_name} (耗时: {duration:.2f}s) {skipped}")
        
    except Exception as e:
        print(f"❌ 任务失败: {task_name} - {e}")
        results[task_name] = {
            "success": False,
            "error": str(e),
            "message": f"任务执行异常: {e}",
            "bizdate": bizdate
        }


def run_pipeline_parallel(bizdate=None, max_workers=3):
    """并行执行流水线（独立任务并行）"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"并行执行量化流水线: {bizdate}")
    print(f"🔧 最大并行数: {max_workers}")
    start_time = datetime.now()
    
    # 导入任务函数
    from quant_pipeline_dag import (
        task_fetch_market_data, 
        task_fetch_financial_data, 
        task_fetch_index_data,
        task_calculate_daily_wide, 
        task_calculate_mv_monthly, 
        task_calculate_forward_returns,
        task_calculate_stock_percentiles,
        task_calculate_market_sentiment_daily,
        task_calculate_market_sentiment_monthly
    )
    
    # 定义任务组和依赖关系
    # 第一阶段：数据获取（完全并行）
    stage1_tasks = {
        'market_data': (task_fetch_market_data, "获取市场数据"),
        'financial_data': (task_fetch_financial_data, "获取财务数据"),
        'index_data': (task_fetch_index_data, "获取指数数据")
    }
    
    # 第二阶段：依赖市场数据的计算（并行）
    stage2_tasks = {
        'daily_wide': (task_calculate_daily_wide, "计算日行情宽表"),
        'mv_monthly': (task_calculate_mv_monthly, "计算月度市值"),
    }
    
    # 第三阶段：依赖日行情宽表的计算（可以并行一部分）
    stage3_tasks = {
        'forward_returns': (task_calculate_forward_returns, "计算未来收益率"),
        'stock_percentiles': (task_calculate_stock_percentiles, "计算历史百分位")
    }
    
    # 第四阶段：依赖历史百分位的计算（并行）
    stage4_tasks = {
        'market_sentiment_daily': (task_calculate_market_sentiment_daily, "计算每日市场热度"),
        'market_sentiment_monthly': (task_calculate_market_sentiment_monthly, "计算按月汇总市场热度")
    }
    
    all_results = {}
    
    try:
        # 第一阶段：并行执行数据获取
        print("\n=== 第一阶段：数据获取（并行执行）===")
        stage1_results = execute_stage_parallel(stage1_tasks, bizdate, max_workers)
        all_results.update(stage1_results)
        
        # 检查关键依赖
        if not stage1_results.get('market_data', {}).get('success', False) and \
           not stage1_results.get('market_data', {}).get('skipped', False):
            print("❌ 市场数据获取失败，终止流水线")
            return finalize_results(all_results, bizdate, start_time)
        
        # 第二阶段：串行执行核心计算
        print("\n=== 第二阶段：核心计算（串行执行）===")
        stage2_results = execute_stage_sequential(stage2_tasks, bizdate)
        all_results.update(stage2_results)
        
        if not stage2_results.get('daily_wide', {}).get('success', False) and \
           not stage2_results.get('daily_wide', {}).get('skipped', False):
            print("❌ 日行情宽表计算失败，终止流水线")
            return finalize_results(all_results, bizdate, start_time)
        
        # 第三阶段：并行执行衍生计算
        print("\n=== 第三阶段：衍生计算（并行执行）===")
        stage3_results = execute_stage_parallel(stage3_tasks, bizdate, max_workers)
        all_results.update(stage3_results)
        
        # 第四阶段：并行执行市场情绪计算
        print("\n=== 第四阶段：市场情绪计算（并行执行）===")
        stage4_results = execute_stage_parallel(stage4_tasks, bizdate, max_workers)
        all_results.update(stage4_results)
        
        return finalize_results(all_results, bizdate, start_time)
        
    except Exception as e:
        print(f"❌ 流水线执行失败: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "bizdate": bizdate, "results": all_results}


def execute_stage_parallel(tasks, bizdate, max_workers):
    """并行执行某个阶段的所有任务"""
    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_task = {}
        for task_key, (task_func, task_name) in tasks.items():
            future = executor.submit(execute_task_async, task_func, bizdate, task_key, results)
            future_to_task[future] = task_name
        
        # 等待所有任务完成
        completed_count = 0
        total_count = len(tasks)
        
        for future in as_completed(future_to_task):
            task_name = future_to_task[future]
            completed_count += 1
            print(f"📊 进度: {completed_count}/{total_count} 任务完成")
    
    return results


def execute_stage_sequential(tasks, bizdate):
    """串行执行某个阶段的任务（用于处理依赖）"""
    results = {}
    
    for task_key, (task_func, task_name) in tasks.items():
        execute_task_async(task_func, bizdate, task_key, results)
    
    return results


def finalize_results(results, bizdate, start_time):
    """整理最终结果"""
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    print("\n" + "="*60)
    print("📊 并行执行结果汇总")
    print("="*60)
    
    success_count = sum(1 for r in results.values() if r and r.get('success'))
    skipped_count = sum(1 for r in results.values() if r and r.get('skipped'))
    total_count = len(results)
    
    task_names = {
        'market_data': '获取市场数据',
        'financial_data': '获取财务数据', 
        'index_data': '获取指数数据',
        'daily_wide': '计算日行情宽表',
        'forward_returns': '计算未来收益率',
        'stock_percentiles': '计算历史百分位',
        'market_sentiment_daily': '计算每日市场热度',
        'market_sentiment_monthly': '计算按月汇总市场热度'
    }
    
    for task_key, result in results.items():
        if result:
            status = "✅" if result.get('success') else "❌"
            if result.get('skipped'):
                status = "⏭️"
            task_name = task_names.get(task_key, task_key)
            message = result.get('message', 'N/A')
            duration_info = f" (耗时: {result.get('execution_duration', 0):.2f}s)" if result.get('execution_duration') else ""
            print(f"{status} {task_name}: {message}{duration_info}")
        else:
            task_name = task_names.get(task_key, task_key)
            print(f"❓ {task_name}: 无执行结果")
    
    print(f"\n⏱️ 总执行时间: {duration:.2f}秒")
    print(f"✅ 成功: {success_count} | ⏭️ 跳过: {skipped_count} | 📊 总计: {total_count}")
    
    if success_count == total_count:
        print("🎉 所有任务执行成功!")
    elif success_count >= total_count * 0.8:
        print("⚠️  大部分任务执行成功，但有部分任务失败")
    else:
        print("❌ 多个任务执行失败，请检查配置")
        
    print("="*60)
    
    return {
        "success": success_count == total_count,
        "bizdate": bizdate,
        "duration": duration,
        "results": results,
        "success_count": success_count,
        "total_count": total_count
    }

def run_pipeline_manual(bizdate=None):
    """手动运行流水线"""
    
    if bizdate is None:
        bizdate = get_today_str()
    
    print(f"手动执行量化流水线: {bizdate}")
    start_time = datetime.now()
    
    # 导入任务函数
    from quant_pipeline_dag import (
        task_fetch_market_data, 
        task_fetch_financial_data, 
        task_fetch_index_data,  # 新增
        task_calculate_daily_wide, 
        task_calculate_mv_monthly,
        task_calculate_forward_returns,
        task_calculate_stock_percentiles,  # 新增
        task_calculate_market_sentiment_daily,  # 新增
        task_calculate_market_sentiment_monthly  # 新增
    )
    
    results = {}
    
    try:
        # 执行任务序列
        print("\n1.1. 获取市场数据...")
        results['market_data'] = task_fetch_market_data(bizdate=bizdate)
        
        print("\n1.2. 获取财务数据...")
        results['financial_data'] = task_fetch_financial_data(bizdate=bizdate)
        
        print("\n1.3. 获取指数数据...")  # 新增
        results['index_data'] = task_fetch_index_data(bizdate=bizdate)
        
        print("\n2.1. 计算日行情宽表...")
        results['daily_wide'] = task_calculate_daily_wide(bizdate=bizdate)

        print("\n2.2. 计算月度市值...")
        results['mv_monthly'] = task_calculate_mv_monthly(bizdate=bizdate)
        
        print("\n3.1. 计算未来收益率...")
        results['forward_returns'] = task_calculate_forward_returns(bizdate=bizdate)
        
        print("\n3.2. 计算历史百分位...")  # 新增
        results['stock_percentiles'] = task_calculate_stock_percentiles(bizdate=bizdate)
        
        print("\n4.1. 计算每日市场热度...")  # 新增
        results['market_sentiment_daily'] = task_calculate_market_sentiment_daily(bizdate=bizdate)
        
        print("\n4.2. 计算按月汇总市场热度...")  # 新增
        results['market_sentiment_monthly'] = task_calculate_market_sentiment_monthly(bizdate=bizdate)
        
        # 计算执行时间
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # 输出结果
        print("\n" + "="*60)
        print("📊 执行结果汇总")
        print("="*60)
        
        success_count = sum(1 for r in results.values() if r and r.get('success'))
        skipped_count = sum(1 for r in results.values() if r and r.get('skipped'))
        total_count = len(results)
        
        # 详细任务状态
        task_names = {
            'market_data': '获取市场数据',
            'financial_data': '获取财务数据', 
            'index_data': '获取指数数据',
            'daily_wide': '计算日行情宽表',
            'mv_monthly': '计算月度宽表',
            'forward_returns': '计算未来收益率',
            'stock_percentiles': '计算历史百分位',
            'market_sentiment_daily': '计算每日市场热度',
            'market_sentiment_monthly': '计算按月汇总市场热度'
        }
        
        for task_key, result in results.items():
            if result:
                status = "✅" if result.get('success') else "❌"
                if result.get('skipped'):
                    status = "⏭️"
                task_name = task_names.get(task_key, task_key)
                message = result.get('message', 'N/A')
                print(f"{status} {task_name}: {message}")
                
                # 如果有错误信息，显示错误详情
                if result.get('error'):
                    print(f"   💡 错误详情: {result['error']}")
            else:
                task_name = task_names.get(task_key, task_key)
                print(f"❓ {task_name}: 无执行结果")
        
        print(f"\n⏱️ 总执行时间: {duration:.2f}秒")
        print(f"✅ 成功: {success_count} | ⏭️ 跳过: {skipped_count} | 📊 总计: {total_count}")
        
        # 执行建议
        if success_count == total_count:
            print("🎉 所有任务执行成功!")
        elif success_count >= total_count * 0.8:  # 80%以上成功率
            print("⚠️  大部分任务执行成功，但有部分任务失败，请检查日志")
        else:
            print("❌ 多个任务执行失败，请检查配置和数据源")
            
        print("="*60)
        
        return {
            "success": success_count == total_count,
            "bizdate": bizdate,
            "duration": duration,
            "results": results,
            "success_count": success_count,
            "total_count": total_count
        }
        
    except Exception as e:
        print(f"❌ 流水线执行失败: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "bizdate": bizdate}

# def run_pipeline_with_dependencies(bizdate=None):
#     """
#     按照依赖关系顺序执行流水线
#     模拟Airflow的依赖执行逻辑
#     """
#     if bizdate is None:
#         bizdate = get_today_str()
    
#     print(f"🔗 按依赖关系执行量化流水线: {bizdate}")
#     start_time = datetime.now()
    
#     from quant_pipeline_dag import (
#         task_fetch_market_data, 
#         task_fetch_financial_data, 
#         task_fetch_index_data,
#         task_calculate_daily_wide, 
#         task_calculate_forward_returns,
#         task_calculate_stock_percentiles,
#         task_calculate_market_sentiment_daily,
#         task_calculate_market_sentiment_monthly
#     )
    
#     results = {}
    
#     try:
#         # 第一阶段：获取数据（可并行）
#         print("\n=== 第一阶段：数据获取 ===")
        
#         print("1. 获取市场数据...")
#         results['market_data'] = task_fetch_market_data(bizdate=bizdate)
        
#         print("2. 获取财务数据...")
#         results['financial_data'] = task_fetch_financial_data(bizdate=bizdate)
        
#         print("3. 获取指数数据...")
#         results['index_data'] = task_fetch_index_data(bizdate=bizdate)
        
#         # 第二阶段：核心计算（依赖市场数据）
#         print("\n=== 第二阶段：核心计算 ===")
        
#         market_success = results.get('market_data', {}).get('success', False)
#         if not market_success and not results.get('market_data', {}).get('skipped'):
#             print("❌ 市场数据获取失败，跳过后续依赖任务")
#             return run_pipeline_manual(bizdate)  # 回退到普通模式
            
#         print("4. 计算日行情宽表...")
#         results['daily_wide'] = task_calculate_daily_wide(bizdate=bizdate)
        
#         # 第三阶段：衍生计算（依赖日行情宽表）
#         print("\n=== 第三阶段：衍生计算 ===")
        
#         daily_wide_success = results.get('daily_wide', {}).get('success', False)
#         if not daily_wide_success and not results.get('daily_wide', {}).get('skipped'):
#             print("❌ 日行情宽表计算失败，跳过后续依赖任务")
#             return run_pipeline_manual(bizdate)  # 回退到普通模式
            
#         print("5. 计算未来收益率...")
#         results['forward_returns'] = task_calculate_forward_returns(bizdate=bizdate)
        
#         print("6. 计算历史百分位...")
#         results['stock_percentiles'] = task_calculate_stock_percentiles(bizdate=bizdate)
        
#         # 第四阶段：市场情绪计算（依赖历史百分位）
#         print("\n=== 第四阶段：市场情绪计算 ===")
        
#         percentiles_success = results.get('stock_percentiles', {}).get('success', False)
#         if not percentiles_success and not results.get('stock_percentiles', {}).get('skipped'):
#             print("❌ 历史百分位计算失败，跳过后续依赖任务")
#             return run_pipeline_manual(bizdate)  # 回退到普通模式
            
#         print("7. 计算每日市场热度...")
#         results['market_sentiment_daily'] = task_calculate_market_sentiment_daily(bizdate=bizdate)
        
#         print("8. 计算按月汇总市场热度...")
#         results['market_sentiment_monthly'] = task_calculate_market_sentiment_monthly(bizdate=bizdate)
        
#         # 输出汇总结果
#         end_time = datetime.now()
#         duration = (end_time - start_time).total_seconds()
        
#         print("\n" + "="*60)
#         print("📊 依赖执行结果汇总")
#         print("="*60)
        
#         success_count = sum(1 for r in results.values() if r and r.get('success'))
#         skipped_count = sum(1 for r in results.values() if r and r.get('skipped'))
#         total_count = len(results)
        
#         print(f"执行业务日期: {bizdate}")
#         print(f"总执行时间: {duration:.2f}秒")
#         print(f"任务完成: {success_count}/{total_count} (跳过: {skipped_count})")
#         print("="*60)
        
#         return {
#             "success": success_count == total_count,
#             "bizdate": bizdate,
#             "duration": duration,
#             "results": results
#         }
        
#     except Exception as e:
#         print(f"❌ 依赖执行失败: {e}")
#         import traceback
#         traceback.print_exc()
#         return {"success": False, "error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="量化流水线执行工具")
    parser.add_argument("--bizdate", type=str, help="业务日期 (YYYYMMDD格式)")
    parser.add_argument("--test", action="store_true", help="测试模式")
    parser.add_argument("--airflow-test", action="store_true", help="测试Airflow DAG")
    parser.add_argument("--parallel", action="store_true", 
                       help="并行执行独立任务（默认串行）")
    parser.add_argument("--max-workers", type=int, default=3,
                       help="最大并行数（默认3）")
    parser.add_argument("--list-tasks", action="store_true", 
                       help="列出所有可用任务")
    
    args = parser.parse_args()
    
    if args.parallel:
        # 并行执行模式
        result = run_pipeline_parallel(args.bizdate, args.max_workers)
        if result["success"]:
            print("🎉 并行流水线执行成功!")
        else:
            print("❌ 并行流水线执行失败!")
    else:
        # 默认串行执行
        result = run_pipeline_manual(args.bizdate)
        if result["success"]:
            print("🎉 流水线执行成功!")
        else:
            print("❌ 流水线执行失败!")