import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
import alphalens as al
import warnings
import pandas as pd
import numpy as np
from scipy import stats
import itertools
from typing import List, Dict, Optional, Tuple, Union
warnings.filterwarnings('ignore')


def _calculate_metrics(long_returns, short_returns, long_short_returns,
                       long_excess_returns, short_excess_returns,
                       periods_per_year=12, risk_free_rate=0.02):
    """
    计算关键收益指标
    """
    metrics = {}
    
    # 1. 多空年化收益
    if len(long_short_returns) > 0:
        metrics['long_short_annual'] = long_short_returns.mean() * periods_per_year
    else:
        metrics['long_short_annual'] = np.nan
    
    # 2. 多空夏普比率
    if len(long_short_returns) > 1 and long_short_returns.std() > 0:
        ls_annual_return = long_short_returns.mean() * periods_per_year
        ls_annual_vol = long_short_returns.std() * np.sqrt(periods_per_year)
        metrics['long_short_sharpe'] = (ls_annual_return - risk_free_rate) / ls_annual_vol
    else:
        metrics['long_short_sharpe'] = np.nan
    
    # 3. 多头年化收益
    if len(long_returns) > 0:
        metrics['long_annual'] = long_returns.mean() * periods_per_year
    else:
        metrics['long_annual'] = np.nan
    
    # 4. 多头夏普比率
    if len(long_returns) > 1 and long_returns.std() > 0:
        long_annual_return = long_returns.mean() * periods_per_year
        long_annual_vol = long_returns.std() * np.sqrt(periods_per_year)
        metrics['long_sharpe'] = (long_annual_return - risk_free_rate) / long_annual_vol
    else:
        metrics['long_sharpe'] = np.nan
    
    # 5. 多头超额年化收益
    if len(long_excess_returns) > 0:
        metrics['long_excess_annual'] = long_excess_returns.mean() * periods_per_year
    else:
        metrics['long_excess_annual'] = np.nan
    
    # 6. 空头超额年化收益
    if len(short_excess_returns) > 0:
        metrics['short_excess_annual'] = short_excess_returns.mean() * periods_per_year
    else:
        metrics['short_excess_annual'] = np.nan
    
    return metrics


def _calculate_ic_hit_ratio(ic_series, ic_mean):
    """
    计算IC胜率
    如果ic_mean为正，统计ic为正的月份比例
    如果ic_mean为负，统计ic为负的月份比例
    """
    if len(ic_series) == 0:
        return np.nan
    
    if ic_mean > 0:
        return float((ic_series > 0).mean())
    else:
        return float((ic_series < 0).mean())


def _calculate_group_monotonicity(returns_by_quantile, ic_mean, n_quantiles):
    """
    计算分位数分组收益的单调性
    
    参数:
    ----------
    returns_by_quantile: DataFrame
        每期每个分位数的收益，index为日期，columns为分位数
    ic_mean: float
        IC均值，用于判断因子方向
    n_quantiles: int
        分组数量
    """
    if returns_by_quantile is None or len(returns_by_quantile.columns) < 2:
        return np.nan
    
    # 计算每个分位数的平均收益
    group_avg_returns = returns_by_quantile.mean()
    
    # 确保分组按顺序排列
    sorted_groups = sorted(group_avg_returns.index)
    sorted_returns = [group_avg_returns[q] for q in sorted_groups]
    
    # 构建自然数序列
    natural_seq = np.arange(1, n_quantiles + 1)
    
    # 根据IC方向调整
    if ic_mean > 0:
        # IC为正，希望分组收益随分位数增加而增加
        target_seq = natural_seq
    else:
        # IC为负，希望分组收益随分位数增加而减少
        target_seq = natural_seq[::-1]
    
    # 计算Spearman相关系数
    if len(sorted_returns) >= 2:
        try:
            # 使用scipy的spearmanr函数
            correlation, _ = stats.spearmanr(sorted_returns, target_seq)
            return float(correlation)
        except Exception as e:
            print(f"计算单调性时出错: {e}")
            return np.nan
    else:
        return np.nan


def _calculate_long_excess_ir(long_excess_returns, periods_per_year=12):
    """
    计算多头超额信息比率
    """
    if len(long_excess_returns) < 2:
        return np.nan
    
    # 年化超额收益
    annual_excess_return = long_excess_returns.mean() * periods_per_year
    
    # 年化跟踪误差
    tracking_error = long_excess_returns.std() * np.sqrt(periods_per_year)
    
    if tracking_error > 0:
        return annual_excess_return / tracking_error
    else:
        return np.nan


def quick_factor_scan(factors_df, factor_cols, return_cols,
                      periods_per_year=12, n_quantiles=10, group_col=None):
    """
    快速因子扫描
    
    参数:
    ----------
    group_col: 分组列名
        None: 不分组统计
        字符串: 按该列分组统计（如年份、行业等）
    """
    all_results = []
    
    for factor in factor_cols:
        for ret in return_cols:
            try:
                # 1. 全样本计算（无论是否分组，都计算total）
                merged_data_total = al.utils.get_clean_factor(
                    factor=factors_df[factor],
                    forward_returns=factors_df[ret].to_frame(),
                    quantiles=n_quantiles
                )
                
                if merged_data_total is None or len(merged_data_total) == 0:
                    print(f"跳过: {factor} - {ret} 数据为空")
                    continue
                
                # 计算全样本IC统计
                ic_series_total = al.performance.factor_information_coefficient(
                    merged_data_total, group_adjust=False, by_group=False
                )
                
                if ic_series_total is None or len(ic_series_total) == 0:
                    continue
                
                ic_mean_total = float(ic_series_total.mean())
                ic_std_total = float(ic_series_total.std())
                ic_hit_ratio_total = _calculate_ic_hit_ratio(ic_series_total, ic_mean_total)
                icir_annual_total = ic_mean_total / ic_std_total * np.sqrt(periods_per_year) if ic_std_total > 0 else np.nan
                
                # 计算全样本收益
                grouped_total = merged_data_total.groupby([
                    merged_data_total.index.get_level_values('date'), 
                    'factor_quantile'
                ])[ret].mean()
                returns_by_quantile_total = grouped_total.unstack(level='factor_quantile')
                
                if len(returns_by_quantile_total.columns) < 2:
                    continue
                
                # 计算分组单调性
                ic_monotonicity_total = _calculate_group_monotonicity(
                    returns_by_quantile_total, ic_mean_total, n_quantiles
                )
                
                # 确定多空方向
                if ic_mean_total > 0:
                    long_q = returns_by_quantile_total.columns.max()
                    short_q = returns_by_quantile_total.columns.min()
                else:
                    long_q = returns_by_quantile_total.columns.min()
                    short_q = returns_by_quantile_total.columns.max()
                
                market_returns_total = merged_data_total.groupby(level='date')[ret].mean()
                
                long_returns_total = returns_by_quantile_total[long_q] if long_q in returns_by_quantile_total.columns else pd.Series(dtype=float)
                short_returns_total = returns_by_quantile_total[short_q] if short_q in returns_by_quantile_total.columns else pd.Series(dtype=float)
                long_short_returns_total = long_returns_total - short_returns_total
                long_excess_returns_total = long_returns_total - market_returns_total
                short_excess_returns_total = short_returns_total - market_returns_total
                
                # 计算收益指标
                metrics_total = _calculate_metrics(
                    long_returns_total, short_returns_total, long_short_returns_total,
                    long_excess_returns_total, short_excess_returns_total,
                    periods_per_year
                )
                
                # 计算多头超额信息比率
                long_excess_ir_total = _calculate_long_excess_ir(long_excess_returns_total, periods_per_year)
                
                result_total = {
                    'factor': factor,
                    'return': ret,
                    'group': 'total',
                    'ic_mean': ic_mean_total,
                    'icir_annual': icir_annual_total,
                    'ic_hit_ratio': ic_hit_ratio_total,
                    'ic_monotonicity': ic_monotonicity_total,
                    'long_short_annual': metrics_total.get('long_short_annual', np.nan),
                    'long_short_sharpe': metrics_total.get('long_short_sharpe', np.nan),
                    'long_excess_annual': metrics_total.get('long_excess_annual', np.nan),
                    'long_excess_ir': long_excess_ir_total,
                }
                
                # 2. 如果指定了分组列，计算分组统计
                group_results = []
                if group_col and group_col in factors_df.columns:
                    # 获取所有分组并排序
                    group_values = sorted(factors_df[group_col].dropna().unique())
                    
                    for group_value in group_values:
                        # 筛选该组数据
                        group_mask = factors_df[group_col] == group_value
                        group_factors = factors_df.loc[group_mask]
                        
                        if len(group_factors) < 10:  # 组内数据太少跳过
                            continue
                        
                        # 准备该组数据
                        merged_data = al.utils.get_clean_factor(
                            factor=group_factors[factor],
                            forward_returns=group_factors[ret].to_frame(),
                            quantiles=n_quantiles
                        )
                        
                        if merged_data is None or len(merged_data) == 0:
                            continue
                        
                        # 计算该组IC统计
                        ic_series = al.performance.factor_information_coefficient(
                            merged_data, group_adjust=False, by_group=False
                        )
                        
                        if ic_series is None or len(ic_series) == 0:
                            continue
                        
                        ic_mean = float(ic_series.mean())
                        ic_std = float(ic_series.std())
                        ic_hit_ratio = _calculate_ic_hit_ratio(ic_series, ic_mean)
                        icir_annual = ic_mean / ic_std * np.sqrt(periods_per_year) if ic_std > 0 else np.nan
                        
                        # 计算该组收益
                        grouped = merged_data.groupby([
                            merged_data.index.get_level_values('date'), 
                            'factor_quantile'
                        ])[ret].mean()
                        returns_by_quantile = grouped.unstack(level='factor_quantile')
                        
                        if len(returns_by_quantile.columns) < 2:
                            continue
                        
                        # 计算分组单调性
                        ic_monotonicity = _calculate_group_monotonicity(
                            returns_by_quantile, ic_mean, n_quantiles
                        )
                        
                        # 确定多空方向
                        if ic_mean > 0:
                            long_q = returns_by_quantile.columns.max()
                            short_q = returns_by_quantile.columns.min()
                        else:
                            long_q = returns_by_quantile.columns.min()
                            short_q = returns_by_quantile.columns.max()
                        
                        market_returns = merged_data.groupby(level='date')[ret].mean()
                        
                        long_returns = returns_by_quantile[long_q] if long_q in returns_by_quantile.columns else pd.Series(dtype=float)
                        short_returns = returns_by_quantile[short_q] if short_q in returns_by_quantile.columns else pd.Series(dtype=float)
                        long_short_returns = long_returns - short_returns
                        long_excess_returns = long_returns - market_returns
                        short_excess_returns = short_returns - market_returns
                        
                        # 计算收益指标
                        metrics = _calculate_metrics(
                            long_returns, short_returns, long_short_returns,
                            long_excess_returns, short_excess_returns,
                            periods_per_year
                        )
                        
                        # 计算多头超额信息比率
                        long_excess_ir = _calculate_long_excess_ir(long_excess_returns, periods_per_year)
                        
                        result = {
                            'factor': factor,
                            'return': ret,
                            'group': str(group_value),
                            'ic_mean': ic_mean,
                            'icir_annual': icir_annual,
                            'ic_hit_ratio': ic_hit_ratio,
                            'ic_monotonicity': ic_monotonicity,
                            'long_short_annual': metrics.get('long_short_annual', np.nan),
                            'long_short_sharpe': metrics.get('long_short_sharpe', np.nan),
                            'long_excess_annual': metrics.get('long_excess_annual', np.nan),
                            'long_excess_ir': long_excess_ir,
                        }
                        group_results.append(result)
                
                # 3. 先添加分组结果（如果有），然后添加total结果
                for result in group_results:
                    all_results.append(result)
                all_results.append(result_total)
                    
            except Exception as e:
                print(f"错误: {factor} - {ret}: {str(e)[:50]}")
                continue
    
    if not all_results:
        return pd.DataFrame()
    
    return pd.DataFrame(all_results)

def screen_factors(df):
    """筛选因子"""
    masks = []

    ##整体排序性
    mask1 = (
        (df['abs_ic_mean'] > 0.04) &
        (df['abs_icir_annual'] > 2) &
        ((df['ic_positive_ratio'] > 0.75) | (df['ic_positive_ratio'] < 0.25))
    )

    ##多头收益，抓好股票
    mask2 = ((df['long_sharpe'] > 0.6) & (df['long_excess_annual'] > 0.05))

    ##空头收益，排坏股票
    mask3 = ((df['short_excess_annual'] < -0.15))
    
    # 条件并集
    result_mask = mask1 | mask2 | mask3
    
    return df[result_mask].copy()

def calculate_factor_intersection_returns(
    factors_df: pd.DataFrame,
    factor1_col: str,
    factor2_col: str,
    return_col: str,
    n_quantiles1: int = 5,
    n_quantiles2: int = 5,
    sequential: bool = False
) -> dict:
    """
    计算双因子交叉分析
    返回包含4个结果DataFrame的字典：
    - excess_return: 超额收益矩阵
    - factor1_median: 因子1中位数矩阵
    - factor2_median: 因子2中位数矩阵
    - sample_share: 样本占比矩阵
    """
    
    # 1. 准备数据
    if isinstance(factors_df.index, pd.MultiIndex):
        data = factors_df.reset_index()
    else:
        data = factors_df.copy()
    
    # 提取需要的列
    needed_cols = ['date', 'asset', factor1_col, factor2_col, return_col]
    data = data[needed_cols].dropna()
    
    if data.empty:
        return {}
    
    # 2. 分组
    if sequential:
        # 顺序排序
        data['group1'] = data.groupby('date')[factor1_col].transform(
            lambda x: pd.qcut(x, q=n_quantiles1, labels=False, duplicates='drop')
        )
        data = data.dropna(subset=['group1'])
        
        def group_factor2(df):
            if len(df) >= n_quantiles2:
                return pd.qcut(df, q=n_quantiles2, labels=False, duplicates='drop')
            else:
                return pd.Series([np.nan] * len(df), index=df.index)
        
        data['group2'] = data.groupby(['date', 'group1'])[factor2_col].transform(group_factor2)
        
        data['group1'] = data['group1'] + 1
        data['group2'] = data['group2'] + 1
        
    else:
        # 独立排序
        data['group1'] = data.groupby('date')[factor1_col].transform(
            lambda x: pd.qcut(x, q=n_quantiles1, labels=False, duplicates='drop')
        ) + 1
        
        data['group2'] = data.groupby('date')[factor2_col].transform(
            lambda x: pd.qcut(x, q=n_quantiles2, labels=False, duplicates='drop')
        ) + 1
    
    data = data.dropna(subset=['group1', 'group2'])
    
    if data.empty:
        return {}
    
    # 3. 计算超额收益
    data['market_return'] = data.groupby('date')[return_col].transform('mean')
    data['excess_return'] = data[return_col] - data['market_return']
    
    # 4. 辅助函数：创建结果矩阵
    def create_matrix(values_col, agg_func='mean'):
        """创建结果矩阵并添加边际值"""
        # 计算交叉矩阵
        matrix = data.pivot_table(
            values=values_col,
            index='group1',
            columns='group2',
            aggfunc=agg_func
        )
        
        # 计算边际值
        row_avg = data.groupby('group1')[values_col].agg(agg_func)
        col_avg = data.groupby('group2')[values_col].agg(agg_func)
        overall_avg = data[values_col].agg(agg_func)
        
        # 添加行平均
        matrix = matrix.copy()
        matrix['Avg'] = row_avg
        
        # 创建最后一行
        last_row = pd.Series(
            {col: col_avg.get(col, np.nan) for col in matrix.columns if col != 'Avg'},
            name='Avg'
        )
        last_row['Avg'] = overall_avg
        
        # 组合结果
        result_matrix = pd.concat([matrix, last_row.to_frame().T])
        
        return result_matrix
    
    # 5. 计算四个矩阵
    excess_return_matrix = create_matrix('excess_return', 'mean')
    factor1_median_matrix = create_matrix(factor1_col, 'median')
    factor2_median_matrix = create_matrix(factor2_col, 'median')
    
    # 6. 计算样本占比矩阵
    # 先计算每个格子的样本数
    sample_count_matrix = data.pivot_table(
        values='excess_return',  # 任意列，只用于计数
        index='group1',
        columns='group2',
        aggfunc='count'
    )
    
    # 计算总样本数
    total_samples = len(data)
    
    # 计算样本占比矩阵
    sample_share_matrix = sample_count_matrix / total_samples
    
    # 计算边际值
    row_share = data.groupby('group1').size() / total_samples
    col_share = data.groupby('group2').size() / total_samples
    
    # 添加行边际
    sample_share_matrix = sample_share_matrix.copy()
    sample_share_matrix['Avg'] = row_share
    
    # 创建最后一行
    last_row = pd.Series(
        {col: col_share.get(col, 0) for col in sample_share_matrix.columns if col != 'Avg'},
        name='Avg'
    )
    last_row['Avg'] = 1.0  # 总占比为100%
    
    # 组合结果
    sample_share_matrix = pd.concat([sample_share_matrix, last_row.to_frame().T])
    
    # 7. 重命名所有矩阵的行列
    index_names = [f'{factor1_col}_Q{int(i)}' if i != 'Avg' else f'{factor1_col}_Avg' 
                   for i in excess_return_matrix.index]
    col_names = [f'{factor2_col}_Q{int(c)}' if c != 'Avg' else f'{factor2_col}_Avg' 
                 for c in excess_return_matrix.columns]
    
    for matrix in [excess_return_matrix, factor1_median_matrix, 
                   factor2_median_matrix, sample_share_matrix]:
        matrix.index = index_names
        matrix.columns = col_names
    
    # 8. 返回结果字典
    return {
        'excess_return': excess_return_matrix,
        'factor1_median': factor1_median_matrix,
        'factor2_median': factor2_median_matrix,
        'sample_share': sample_share_matrix
    }
def calculate_factor_intersection_returns_with_bins(
    factors_df: pd.DataFrame,
    factor1_col: str,
    factor2_col: str,
    return_col: str,
    bins1: Union[List[float], np.ndarray],
    bins2: Union[List[float], np.ndarray]
) -> dict:
    """
    优化版本：使用向量化操作计算双因子交叉分组的超额收益矩阵
    避免逐日循环，大幅提高效率
    
    返回包含4个结果DataFrame的字典：
    - excess_return: 超额收益矩阵
    - factor1_median: 因子1中位数矩阵
    - factor2_median: 因子2中位数矩阵
    - sample_share: 样本占比矩阵
    """
    
    # 1. 准备数据
    if isinstance(factors_df.index, pd.MultiIndex):
        data = factors_df.reset_index()
    else:
        data = factors_df.copy()
    
    # 检查必要的列
    if 'date' not in data.columns:
        data['date'] = 1
    if 'asset' not in data.columns:
        data['asset'] = data.index
    
    # 提取需要的列
    needed_cols = ['date', 'asset', factor1_col, factor2_col, return_col]
    data = data[needed_cols].dropna()
    
    if data.empty:
        return {}
    
    # 2. 检查bins的有效性
    if len(bins1) < 2 or len(bins2) < 2:
        raise ValueError("bins必须包含至少2个边界值")
    
    # 3. 使用groupby.apply进行向量化分组
    def assign_groups(group_df):
        """为单个日期分组分配分组标签"""
        try:
            # 对因子1分组
            factor1_group = pd.cut(
                group_df[factor1_col],
                bins=bins1,
                labels=range(1, len(bins1)),
                include_lowest=True
            )
            
            # 对因子2分组
            factor2_group = pd.cut(
                group_df[factor2_col],
                bins=bins2,
                labels=range(1, len(bins2)),
                include_lowest=True
            )
            
            # 返回分组标签
            group_df['group1'] = factor1_group
            group_df['group2'] = factor2_group
            
        except Exception as e:
            # 如果分组失败，返回NaN
            group_df['group1'] = np.nan
            group_df['group2'] = np.nan
        
        return group_df
    
    # 应用分组函数
    data = data.groupby('date', group_keys=False).apply(assign_groups)
    
    # 4. 移除无效分组
    data = data.dropna(subset=['group1', 'group2'])
    if data.empty:
        return {}
    
    # 5. 计算超额收益
    data['market_return'] = data.groupby('date')[return_col].transform('mean')
    data['excess_return'] = data[return_col] - data['market_return']
    
    # 6. 辅助函数：创建结果矩阵
    def create_matrix(values_col, agg_func='mean'):
        """创建结果矩阵并添加边际值"""
        # 计算交叉矩阵
        matrix = data.pivot_table(
            values=values_col,
            index='group1',
            columns='group2',
            aggfunc=agg_func
        )
        
        # 计算边际值
        row_avg = data.groupby('group1')[values_col].agg(agg_func)
        col_avg = data.groupby('group2')[values_col].agg(agg_func)
        overall_avg = data[values_col].agg(agg_func)
        
        # 添加行平均
        matrix = matrix.copy()
        matrix['Avg'] = row_avg
        
        # 创建最后一行
        last_row = pd.Series(
            {col: col_avg.get(col, np.nan) for col in matrix.columns if col != 'Avg'},
            name='Avg'
        )
        last_row['Avg'] = overall_avg
        
        # 组合结果
        result_matrix = pd.concat([matrix, last_row.to_frame().T])
        
        return result_matrix
    
    # 7. 计算四个矩阵
    excess_return_matrix = create_matrix('excess_return', 'mean')
    factor1_median_matrix = create_matrix(factor1_col, 'median')
    factor2_median_matrix = create_matrix(factor2_col, 'median')
    
    # 8. 计算样本占比矩阵
    # 先计算每个格子的样本数
    sample_count_matrix = data.pivot_table(
        values='excess_return',  # 任意列，只用于计数
        index='group1',
        columns='group2',
        aggfunc='count'
    )
    
    # 计算总样本数
    total_samples = len(data)
    
    # 计算样本占比矩阵
    sample_share_matrix = sample_count_matrix / total_samples
    
    # 计算边际值
    row_share = data.groupby('group1').size() / total_samples
    col_share = data.groupby('group2').size() / total_samples
    
    # 添加行边际
    sample_share_matrix = sample_share_matrix.copy()
    sample_share_matrix['Avg'] = row_share
    
    # 创建最后一行
    last_row = pd.Series(
        {col: col_share.get(col, 0) for col in sample_share_matrix.columns if col != 'Avg'},
        name='Avg'
    )
    last_row['Avg'] = 1.0  # 总占比为100%
    
    # 组合结果
    sample_share_matrix = pd.concat([sample_share_matrix, last_row.to_frame().T])
    
    # 9. 重命名所有矩阵的行列
    index_names = [f'{factor1_col}_Bin{int(i)}' if i != 'Avg' else f'{factor1_col}_Avg' 
                   for i in excess_return_matrix.index]
    col_names = [f'{factor2_col}_Bin{int(c)}' if c != 'Avg' else f'{factor2_col}_Avg' 
                 for c in excess_return_matrix.columns]
    
    for matrix in [excess_return_matrix, factor1_median_matrix, 
                   factor2_median_matrix, sample_share_matrix]:
        matrix.index = index_names
        matrix.columns = col_names
    
    # 10. 返回结果字典
    return {
        'excess_return': excess_return_matrix,
        'factor1_median': factor1_median_matrix,
        'factor2_median': factor2_median_matrix,
        'sample_share': sample_share_matrix
    }


def sequential_qcut_simple(
    df: pd.DataFrame,
    a_col: str,
    b_col: str,
    q1: int = 5,
    q2: int = 5
) -> pd.DataFrame:
    """
    对DataFrame的两列进行顺序排序分箱
    先对a_col分q1组，再在每组内对b_col分q2组
    返回添加了分组标签的原始数据
    """
    
    # 复制数据避免修改原数据
    data = df.copy()
    
    # 移除缺失值
    data = data.dropna(subset=[a_col, b_col])
    
    if data.empty:
        return data
    
    # 创建分组列名
    a_group_col = f"{a_col}_bin"
    b_group_col = f"{b_col}_bin"
    
    # 第一步：对每个日期内的a_col进行分位数分组
    data[a_group_col] = data.groupby('date')[a_col].transform(
        lambda x: pd.qcut(x, q=q1, labels=range(1, q1+1), duplicates='drop')
    )
    
    # 移除a_group为NaN的行
    data = data.dropna(subset=[a_group_col])
    
    if data.empty:
        return data
    
    # 第二步：对每个日期和a组的组合内的b_col进行分位数分组
    def qcut_within_group(series, q):
        """在组内进行qcut，处理样本不足的情况"""
        if len(series) < q:
            return pd.Series([np.nan] * len(series), index=series.index)
        return pd.qcut(series, q=q, labels=range(1, q+1), duplicates='drop')
    
    data[b_group_col] = data.groupby(['date', a_group_col])[b_col].transform(
        lambda x: qcut_within_group(x, q2)
    )
    
    # 移除b_group为NaN的行
    data = data.dropna(subset=[b_group_col])
    
    return data

def evaluate_factor_combinations(
    df: pd.DataFrame,
    factor_list: List[str],
    return_cols: List[str],
    combination_size: int = 3,
    combo_prefix: str = 'combo',
    output_all_results: bool = True,
    n_quantiles: int = 10,
    group_col: Optional[str] = None,
    periods_per_year: int = 12
) -> pd.DataFrame:
    """
    评估因子所有组合的绩效
    
    参数:
    ----------
    df: DataFrame
        包含因子和收益的数据框
    factor_list: list
        因子列名列表
    return_cols: list
        收益列名列表
    combination_size: int, default=2
        组合大小，默认2个因子组合
    combo_prefix: str, default='combo'
        组合因子列名前缀
    output_all_results: bool, default=True
        是否输出所有组合的详细结果
    n_quantiles: int, default=10
        分位数数量
    group_col: str, optional
        分组列名
    periods_per_year: int, default=12
        年化周期数
        
    返回:
    ----------
    DataFrame: 所有组合的评估结果
    """
    
    # 检查输入
    missing_factors = [f for f in factor_list if f not in df.columns]
    if missing_factors:
        raise ValueError(f"以下因子不在数据框中: {missing_factors}")
    
    # 生成所有组合
    all_combinations = list(itertools.combinations(factor_list, combination_size))
    n_combinations = len(all_combinations)
    
    print(f"开始评估 {len(factor_list)} 个因子的 {combination_size} 组合")
    print(f"总组合数: {n_combinations}")
    print(f"因子列表: {factor_list}")
    print("-" * 50)
    
    # 存储结果
    all_results = []
    
    for i, combo in enumerate(all_combinations, 1):
        combo_name = f"{combo_prefix}_{i}"
        combo_factors = list(combo)
        combo_factors_str = ",".join(combo_factors)  # 逗号连接的因子名
        
        print(f"正在评估组合 {i}/{n_combinations}: {combo_factors}")
        
        try:
            # 1. 创建组合因子（等权平均）
            # 因子已经归一化到[0,1]，直接等权平均
            df[combo_name] = df[combo_factors].mean(axis=1)
            
            # 2. 评估组合因子
            result_df = quick_factor_scan(
                df, 
                [combo_name],  # 只评估当前组合
                return_cols,
                periods_per_year=periods_per_year,
                n_quantiles=n_quantiles,
                group_col=group_col
            )
            
            if not result_df.empty:
                # 只保留total组的结果
                total_result = result_df[result_df['group'] == 'total']
                
                if not total_result.empty:
                    # 添加组合信息
                    total_result = total_result.copy()
                    total_result.insert(0, 'combo_factors', combo_factors_str)  # 放在第一列
                    total_result['n_factors'] = combination_size
                    total_result['combo_name'] = combo_name
                    
                    all_results.append(total_result)
                    
                    # 删除临时列
                    df.drop(columns=[combo_name], inplace=True)
            
        except Exception as e:
            print(f"评估组合 {combo_factors} 时出错: {str(e)[:100]}")
            # 确保删除临时列
            if combo_name in df.columns:
                df.drop(columns=[combo_name], inplace=True)
            continue
    
    # 合并所有结果
    if all_results:
        combined_results = pd.concat(all_results, ignore_index=True)
        
        # 重新排序列顺序
        col_order = ['combo_factors', 'return', 'group', 
                     'ic_mean', 'icir_annual', 'ic_hit_ratio', 'ic_monotonicity',
                     'long_short_annual', 'long_short_sharpe', 'long_annual', 'long_sharpe',
                     'long_excess_annual', 'long_excess_ir']
        
        # 只保留存在的列
        col_order = [col for col in col_order if col in combined_results.columns]
        combined_results = combined_results[col_order]
        
        # 按IC降序排列
        if 'ic_mean' in combined_results.columns:
            combined_results = combined_results.sort_values('ic_mean', ascending=False)
        
        # 输出最佳组合
        if not combined_results.empty:
            print(f"\n评估完成! 共成功评估 {len(combined_results)} 个组合")
            print("\n最佳组合 (按IC排序):")
            print("-" * 50)
            
            for i, (_, row) in enumerate(combined_results.head(5).iterrows(), 1):
                print(f"\n第{i}名:")
                print(f"  组合因子: {row['combo_factors']}")
                print(f"  IC均值: {row['ic_mean']:.4f}")
                print(f"  ICIR: {row['icir_annual']:.4f}")
                print(f"  IC胜率: {row['ic_hit_ratio']:.2%}")
                print(f"  IC单调性: {row['ic_monotonicity']:.2%}")
                print(f"  多空夏普: {row['long_short_sharpe']:.4f}")
                print(f"  多头超额IR: {row['long_excess_ir']:.4f}")
            
            # 因子出现频率
            print(f"\n因子在最佳组合中的出现频率 (前5名组合):")
            print("-" * 50)
            top_combos = combined_results.head(5)['combo_factors'].tolist()
            factor_counts = {}
            
            for combo in top_combos:
                factors = combo.split(',')
                for factor in factors:
                    factor_counts[factor] = factor_counts.get(factor, 0) + 1
            
            for factor, count in sorted(factor_counts.items(), key=lambda x: x[1], reverse=True):
                print(f"  {factor}: {count} 次")
        
        return combined_results
    else:
        print("警告: 没有任何组合评估成功")
        return pd.DataFrame()

def get_merged_factor_ic(factors_df, factor_cols=None, returns_col='20D', quantiles=10):
    """
    快速获取合并的因子IC序列DataFrame
    
    参数:
    ----------
    factors_df : pd.DataFrame
        包含因子和收益率的DataFrame，索引为MultiIndex [日期, 资产]
    returns_col : str, 默认='20D'
        收益率列名
    factor_cols : list, 可选
        因子列名列表，如果为None则使用所有非收益率列
    quantiles : int, 默认=10
        分组数量
    
    返回:
    -------
    pd.DataFrame
        合并的IC序列，列为因子名，索引为日期
    """
    
    if factor_cols is None:
        # 自动识别因子列（排除收益率列）
        factor_cols = [col for col in factors_df.columns if col != returns_col]
    
    # 准备收益率DataFrame
    returns_df = factors_df[[returns_col]]
    
    ic_data = {}
    
    for factor in factor_cols:
        try:
            # 获取clean factor数据
            merged_data = al.utils.get_clean_factor(
                factors_df[factor],
                returns_df,
                quantiles=quantiles
            )
            
            # 计算IC序列
            ic_df = al.performance.factor_information_coefficient(merged_data)
            
            # 提取20D IC（如果没有20D，使用第一列）
            if '20D' in ic_df.columns:
                ic_data[factor] = ic_df['20D']
            elif 20 in ic_df.columns:
                ic_data[factor] = ic_df[20]
            else:
                ic_data[factor] = ic_df.iloc[:, 0]
                
        except Exception as e:
            print(f"因子 {factor} 计算失败: {e}")
            continue
    
    # 合并所有IC序列
    merged_ic = pd.DataFrame(ic_data)
    
    return merged_ic