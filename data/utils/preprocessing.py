import numpy as np
import pandas as pd
import statsmodels.api as sm


def mad_winsorize(df, factor_col, date_col='date', group_col=None, n=5):
    def mad_winsorize_series(series, n):
        median = series.median()
        mad = (series - median).abs().median()
        if mad == 0:
            return series
        lower_bound = median - n * mad
        upper_bound = median + n * mad
        return series.clip(lower=lower_bound, upper=upper_bound)
    
    if group_col:
        groups = [date_col, group_col]
    else:
        groups = date_col
        
    return df.groupby(groups)[factor_col].transform(lambda x: mad_winsorize_series(x, n))


def standardize_factor(df, factor_col, date_col='date', group_col=None, method='zscore'):
    result = pd.Series(index=df.index, dtype=float)
    
    if group_col:
        groups = [date_col, group_col]
    else:
        groups = date_col
    
    for name, group in df.groupby(groups):
        values = group[factor_col]
        
        if method == 'zscore':
            if values.std() == 0 or len(values) < 2:
                standardized = 0
            else:
                standardized = (values - values.mean()) / values.std()
                
        elif method == 'mad':
            median = values.median()
            mad = (values - median).abs().median()
            if mad == 0 or len(values) < 2:
                standardized = 0
            else:
                standardized = (values - median) / mad
        else:
            raise ValueError("method参数必须是'zscore'或'mad'")
        
        result.loc[group.index] = standardized
    
    return result


def quantile_factor(df, factor_col, date_col='date', group_col=None, n_quantiles=5, ascending=True):
    result = pd.Series(index=df.index, dtype='category')
    
    if group_col:
        groups = [date_col, group_col]
    else:
        groups = date_col
    
    for name, group in df.groupby(groups):
        values = group[factor_col]
        
        if len(values) < n_quantiles:
            result.loc[group.index] = np.nan
            continue
        
        if ascending:
            quantiles = pd.qcut(
                values, 
                q=n_quantiles, 
                labels=range(1, n_quantiles + 1),
                duplicates='drop'
            )
        else:
            quantiles = pd.qcut(
                -values, 
                q=n_quantiles, 
                labels=range(1, n_quantiles + 1),
                duplicates='drop'
            )
        
        result.loc[group.index] = quantiles
    
    return result


def rank_factor(df, factor_col, date_col='date', group_col=None, n_quantiles=5, ascending=True, pct=True):
    result = pd.Series(index=df.index, dtype=float)
    
    if group_col:
        groups = [date_col, group_col]
    else:
        groups = date_col
    
    for name, group in df.groupby(groups):
        values = group[factor_col]
        
        if ascending:
            ranks = values.rank(method='average', na_option='keep')
        else:
            ranks = (-values).rank(method='average', na_option='keep')
        
        if pct and len(values) > 1:
            ranked = (ranks - 1) / (len(values) - 1)
        elif pct:
            ranked = 0.5
        else:
            ranked = ranks
        
        result.loc[group.index] = ranked
    
    return result


def neutralize_factor(
    df,
    factor_col,
    mv_col='circ_mv',
    date_col='date',
    industry_col='l1_code',
    weight_type='log_mv',
    mv_processing='log1p',
    winsorize_n=5
):
    df = df.copy()
    
    df[f'{factor_col}_clean'] = mad_winsorize(
        df, factor_col, date_col=date_col, n=winsorize_n
    )
    
    df[f'{mv_col}_clean'] = mad_winsorize(
        df, mv_col, date_col=date_col, n=winsorize_n
    )
    
    if mv_processing == 'log1p':
        df['mv_feature'] = np.log1p(df[f'{mv_col}_clean'])
    elif mv_processing == 'log':
        df['mv_feature'] = np.log(df[f'{mv_col}_clean'].clip(lower=1e-6))
    elif mv_processing == 'sqrt':
        df['mv_feature'] = np.sqrt(df[f'{mv_col}_clean'])
    
    if weight_type == 'equal':
        df['weight'] = 1.0
    elif weight_type == 'mv':
        df['weight'] = df[f'{mv_col}_clean']
    elif weight_type == 'log_mv':
        df['weight'] = df['mv_feature']
    elif weight_type == 'sqrt_mv':
        df['weight'] = np.sqrt(df[f'{mv_col}_clean'])
    
    neutralized = pd.Series(index=df.index, dtype=float)
    
    for date, group in df.groupby(date_col):
        required_cols = [f'{factor_col}_clean', 'mv_feature', 'weight']
        if industry_col:
            required_cols.append(industry_col)
        
        clean_mask = group[required_cols].notna().all(axis=1)
        clean_group = group[clean_mask]
        
        if len(clean_group) < 10:
            neutralized.loc[clean_group.index] = clean_group[f'{factor_col}_clean']
            continue
        
        X = clean_group[['mv_feature']].copy()
        
        if industry_col:
            industry_dummies = pd.get_dummies(
                clean_group[industry_col], prefix='ind', drop_first=True
            )
            X = pd.concat([X, industry_dummies], axis=1)
        
        X = sm.add_constant(X)
        y = clean_group[f'{factor_col}_clean']
        weights = clean_group['weight'].values
        
        try:
            model = sm.WLS(y, X, weights=weights)
            results = model.fit()
            residuals = y - results.predict(X)
            
            if len(residuals) > 1:
                residuals = (residuals - residuals.mean()) / residuals.std()
            
            neutralized.loc[clean_group.index] = residuals
            
            corr_mv = pd.Series(residuals).corr(clean_group['mv_feature'])
            if abs(corr_mv) > 0.05:
                print(f"警告: {date} 中性化后与市值相关性仍较高: {corr_mv:.4f}")
                
        except Exception as e:
            print(f"{date} 回归失败: {e}")
            neutralized.loc[clean_group.index] = y
    
    return neutralized


def orthogonalize_factor(
    df,
    target_factor,
    control_factors,
    date_col='date',
    return_residual=True,
    add_intercept=True,
    method='linear',
    **kwargs
):
    df = df.copy()
    
    if not control_factors:
        raise ValueError("control_factors 不能为空")
    
    if target_factor in control_factors:
        raise ValueError(f"目标因子 {target_factor} 不应出现在控制因子中")
    
    result = pd.Series(index=df.index, dtype=float, name=f"{target_factor}_orth")
    
    for date, group in df.groupby(date_col):
        clean_group = group.dropna(subset=[target_factor] + control_factors)
        
        if len(clean_group) < 2:
            result.loc[group.index] = group[target_factor]
            continue
        
        X = clean_group[control_factors].values
        y = clean_group[target_factor].values
        
        if add_intercept:
            X = np.column_stack([np.ones(len(X)), X])
        
        try:
            if method == 'linear':
                if add_intercept:
                    beta = np.linalg.lstsq(X, y, rcond=None)[0]
                    y_pred = X @ beta
                else:
                    beta = np.linalg.lstsq(X, y, rcond=None)[0]
                    y_pred = X @ beta
                
                if return_residual:
                    y_orth = y - y_pred
                else:
                    y_orth = y_pred
                
            elif method == 'partial':
                y_orth = y.copy()
                
                for i, factor in enumerate(control_factors):
                    X_i = clean_group[factor].values.reshape(-1, 1)
                    if add_intercept:
                        X_i = np.column_stack([np.ones(len(X_i)), X_i])
                    
                    beta_i = np.linalg.lstsq(X_i, y_orth, rcond=None)[0]
                    y_pred_i = X_i @ beta_i
                    y_orth = y_orth - y_pred_i
                
                if not return_residual:
                    y_orth = y - y_orth
                    
            elif method == 'residual':
                if add_intercept:
                    beta = np.linalg.lstsq(X, y, rcond=None)[0]
                    y_pred = X @ beta
                else:
                    beta = np.linalg.lstsq(X, y, rcond=None)[0]
                    y_pred = X @ beta
                
                y_orth = y - y_pred
                
                if not return_residual:
                    y_orth = y_pred
            else:
                raise ValueError(f"不支持的 method: {method}")
            
            result.loc[clean_group.index] = y_orth
            
        except np.linalg.LinAlgError:
            result.loc[clean_group.index] = clean_group[target_factor]
            continue
        
        missing_idx = group.index.difference(clean_group.index)
        if not missing_idx.empty:
            result.loc[missing_idx] = group.loc[missing_idx, target_factor]
    
    return result