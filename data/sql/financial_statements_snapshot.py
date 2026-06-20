import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, Any
from itertools import product
from sqlalchemy import text
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# 导入基类
from data.config.database import save_to_database
from data.config.database import engine as global_engine
from data.utils.base_calculator import BaseCalculator

class FinancialStatementsSnapshotCalculator(BaseCalculator):
    
    def __init__(self, engine=None, lookback_years=4):
        """
        财务数据处理类
        
        参数:
        engine: SQLAlchemy引擎
        lookback_years: 回溯年数
        """
        # 调用基类构造函数
        if engine is None:
            engine = global_engine
            
        super().__init__("FinancialStatementsSnapshotCalculator", engine=engine)
        
        # 设置默认参数
        self.lookback_years = lookback_years ##一般用过去3年历史，冗余一点，保证有三年数据
        
        # 设置默认表名和写入模式
        self.default_table_name = 'financial_statements_snapshot'
        self.default_write_mode = 'overwrite'

        self._init_column_lists()
        
        self.logger.info(f"FinancialStatementsSnapshotCalculator，回溯{lookback_years}年")

    def _init_column_lists(self):
        """初始化三张表需要的列"""
        self.id_columns = ['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'update_flag'] 
        
        self.bs_content_columns = [
            'cap_rese', 'undistr_porfit', 'surplus_rese', \
            # 资产
            'money_cap', 'trad_asset', 'notes_receiv', 'accounts_receiv', 'prepayment', 'oth_receiv', \
            'inventories', 'contract_assets', 'nca_within_1y', 'oth_cur_assets', 'total_cur_assets', \
            'lt_rec', 'lt_eqt_invest', 'oth_illiq_fin_assets', 'invest_real_estate', \
            'fix_assets', 'cip', 'use_right_assets', \
            'intan_assets', 'r_and_d', 'goodwill', 'lt_amor_exp', 'defer_tax_assets', \
            'oth_nca', 'total_nca', 'total_assets', \
            # 负债
            'st_borr', 'trading_fl', \
            'notes_payable', 'acct_payable', 'adv_receipts', 'contract_liab', 'payroll_payable', 'taxes_payable', 'oth_payable', \
            'non_cur_liab_due_1y', 'oth_cur_liab', 'total_cur_liab', \
            'lt_borr', 'bond_payable', 'lease_liab', 'estimated_liab', 'defer_tax_liab', \
            'oth_ncl', 'total_ncl', 'total_liab', \
            # 权益
            'minority_int', 'total_hldr_eqy_exc_min_int', 'total_hldr_eqy_inc_min_int', 'total_liab_hldr_eqy', \
        ]
        
        self.inc_content_columns = [
            # 收入和利润
        	'total_revenue', 'revenue', \
            'total_cogs', 'oper_cost', 'biz_tax_surchg', 'sell_exp', 'admin_exp', 'rd_exp', \
            'fin_exp', 'fin_exp_int_exp', 'fin_exp_int_inc', \
            'oth_income', 'invest_income', 'ass_invest_income', 'fv_value_chg_gain', \
        	'assets_impair_loss', 'credit_impa_loss', 'asset_disp_income', 
        	'operate_profit', 'non_oper_income', 'non_oper_exp', \
        	'total_profit', 'income_tax', 'n_income', 'n_income_attr_p', 'minority_gain',
            'oth_compr_income', 't_compr_income', 'compr_inc_attr_p',
            # EBIT/EBITDA
            'ebit', 'ebitda',
        ]

        self.cf_content_columns = [
        	# 经营活动现金流量
        	'net_profit', 'finan_exp', 'c_fr_sale_sg', 'recp_tax_rends', 'c_fr_oth_operate_a', 'c_inf_fr_operate_a', 
        	'c_paid_goods_s', 'c_paid_to_for_empl', 'c_paid_for_taxes', 'oth_cash_pay_oper_act', 'st_cash_out_act', 'n_cashflow_act',     
        	# 投资活动现金流量
        	'oth_recp_ral_inv_act', 'c_disp_withdrwl_invest', 'c_recp_return_invest', 'n_recp_disp_fiolta', 'n_recp_disp_sobu', 
        	'stot_inflows_inv_act', 'c_pay_acq_const_fiolta', 'c_paid_invest', 'n_disp_subs_oth_biz', 'oth_pay_ral_inv_act', 
        	'stot_out_inv_act', 'n_cashflow_inv_act',     
        	# 筹资活动现金流量
        	'c_recp_borrow', 'c_recp_cap_contrib', 'incl_cash_rec_saims', 'oth_cash_recp_ral_fnc_act', 'stot_cash_in_fnc_act', 
        	'free_cashflow', 'c_prepay_amt_borr', 'c_pay_dist_dpcp_int_exp', 'incl_dvd_profit_paid_sc_ms', #'proc_issue_bonds',
        	'oth_cashpay_ral_fnc_act', 'stot_cashout_fnc_act', 'n_cash_flows_fnc_act',     
        	# 其他重要项目
        	'eff_fx_flu_cash', 'n_incr_cash_cash_equ', 'c_cash_equ_beg_period', 'c_cash_equ_end_period', 
            'depr_fa_coga_dpba', 'amort_intang_assets', 'lt_amort_deferred_exp', 'decr_inventories', 'decr_oper_payable', 'incr_oper_payable',
    	]

        # 资产负债表半年报/年报更新列，前向填充1格
        self.mrq_columns = [
            'oth_receiv', 'fix_assets', 'cip', 'oth_payable',
        ]

        # 利润表/资产负债表半年报/年报更新列，取最近一年的填充3各
        self.mry_columns = [
            'ebitda', 'net_profit', 'finan_exp', 'depr_fa_coga_dpba', 'amort_intang_assets', 'lt_amort_deferred_exp', \
            'decr_inventories', 'decr_oper_payable', 'incr_oper_payable',
        ]
        
    def get_data(
        self, 
        snapshot_date: str,
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> Dict[str, pd.DataFrame]:
        """
        获取财报披露日期+三张表+分红+月度MV
        
        参数:
        snapshot_date: 观察日期 (yyyymmdd格式)
        entity_list: 股票代码列表
        
        返回:
        包含五张表数据的字典
        """
        # 转换日期格式
        snapshot_dt = pd.to_datetime(snapshot_date, format='%Y%m%d')
        
        start_date = (snapshot_dt - pd.DateOffset(years=self.lookback_years)).strftime('%Y%m%d')
        end_date = snapshot_dt.strftime('%Y%m%d')
        
        self.logger.info(f"获取财务数据: 观察日={snapshot_date}, 范围={start_date}~{end_date}")
        
        # 定义查询模板，要保证在观察日所有内容列都可获得
        queries = {
            'disclosure_date': """
                SELECT * FROM disclosure_date 
                WHERE end_date >= '{start_date}' 
                AND end_date <= '{end_date}'
                AND (actual_date <= '{end_date}' OR modify_date <= '{end_date}')
            """,
            'balancesheet': """
                SELECT * FROM balancesheet 
                WHERE end_date >= '{start_date}' 
                AND end_date <= '{end_date}'
                AND f_ann_date <= '{end_date}'
            """,
            'income': """
                SELECT * FROM income 
                WHERE end_date >= '{start_date}' 
                AND end_date <= '{end_date}'
                AND f_ann_date <= '{end_date}'
            """,
            'cashflow': """
                SELECT * FROM cashflow 
                WHERE end_date >= '{start_date}' 
                AND end_date <= '{end_date}'
                AND f_ann_date <= '{end_date}'
            """,
            'dividend': """
                SELECT t.ts_code, t.end_date, t.ex_date, t.cash_div, t.base_share
                FROM
                (
                SELECT ts_code, end_date, ex_date, cash_div, base_share,
                ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY update_flag DESC) rn 
                FROM dividend 
                WHERE end_date >= '{start_date}' 
                AND end_date <= '{end_date}'
                AND ex_date <= '{end_date}'
                AND div_proc='实施'
                AND cash_div>0
                ) t
                WHERE t.rn=1
            """,
            'mv':"""
                SELECT * FROM mv_monthly 
                WHERE trade_date >= '{start_date}' 
                AND trade_date <= '{end_date}'
            """
        }
        
        data = {}

        # 获取六张表
        for table_name, query_template in queries.items():
            query = query_template.format(start_date=start_date, end_date=end_date)
            
            if entity_list:
                codes_str = ",".join([f"'{code}'" for code in entity_list])
                query += f" AND ts_code IN ({codes_str})"
            
            try:
                df = pd.read_sql(query, self.engine)
                self.logger.info(f"读取{table_name}: {len(df):,} 条")
                data[table_name] = df
            except Exception as e:
                self.logger.error(f"读取{table_name}失败: {e}")
                data[table_name] = pd.DataFrame()
            
        return data
    
    def process_data(
        self, 
        data: Dict[str, pd.DataFrame], 
        snapshot_date: str,
        **kwargs
    ) -> pd.DataFrame:
        """
        处理财务数据，包括去重、合并、清理和计算当季值
        参数:
        data: 包含五张表数据的字典
        snapshot_date: 观察日期
        返回:
        处理后的DataFrame
        """
        self.logger.info(f"开始处理财务数据，观察日: {snapshot_date}")
        
        if not data or data['disclosure_date'].empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        # 1. 财报披露日扩充范围内所有日期（方便统计财报缺失情况、严格计算TTM等）
        self.logger.info("扩展财报日期...")
        data['disclosure_date'] = self._expand_financial_dates(data['disclosure_date'])

        # 2. 保留关键列
        self.logger.info("保留关键列...")
        data['balancesheet'] = data['balancesheet'].loc[:,self.id_columns+self.bs_content_columns]
        data['income'] = data['income'].loc[:,self.id_columns+self.inc_content_columns]
        data['cashflow'] = data['cashflow'].loc[:,self.id_columns+self.cf_content_columns]
        
        # 3. 三张表去重
        self.logger.info("三张表去重...")
        for table in ['balancesheet', 'income', 'cashflow']:
            if not data[table].empty:
                data[table] = self._deduplicate_table(data[table], table)
     
        # 4. 合并
        self.logger.info("合并六张表...")
        merged = self._merge_tables(data, snapshot_date)

        # 5. 加工衍生列
        self.logger.info("加工衍生列...")
        merged = self._process_columns(merged)

        # 6. 调整输出顺序
        merged['snapshot_date'] = pd.to_datetime(snapshot_date)
        
        all_columns = list(merged.columns)
        key_columns = [
            'snapshot_date', 'ts_code', 'ann_date', 'end_date', 'pre_date', 'actual_date', 'modify_date', 'report_type', \
            'has_bs', 'has_inc', 'has_cf', 'has_div', 'bs_f_ann_date', 'inc_f_ann_date', 'cf_f_ann_date', 'ex_date'
        ]
        mv_columns = ['total_share', 'float_share', 'total_mv', 'circ_mv', 'ev', 'evnoa']
        div_columns = ['cash_div', 'base_share', 'total_div']
        other_columns = [col for col in all_columns if col not in key_columns+mv_columns+div_columns]
        
        final_order = key_columns + mv_columns + div_columns + other_columns
        
        return merged[final_order]

    def _expand_financial_dates(self, df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy()
        df['end_date'] = pd.to_datetime(df['end_date'])
        
        # 为每只股票创建日期范围
        def create_date_range_for_group(group):
            min_date = group['end_date'].min()
            max_date = group['end_date'].max()
            
            # 创建季度末日期序列
            dates = pd.date_range(start=min_date,end=max_date,freq='Q')
            
            return pd.DataFrame({'ts_code': group['ts_code'].iloc[0], 'end_date': dates})
        
        # 为每只股票生成日期范围
        date_ranges = df.groupby('ts_code').apply(create_date_range_for_group).reset_index(drop=True)
        
        # 合并回原数据
        result = pd.merge(
            date_ranges,
            df,
            on=['ts_code', 'end_date'],
            how='left',
            suffixes=('', '')
        )
        
        return result
    
    def _deduplicate_table(self, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
        """单个表去重"""
        # 确保日期格式
        date_cols = ['end_date', 'f_ann_date', 'ann_date']
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        
        # 标记版本号和延迟天数（非必要，数据分析用）
        df = df.sort_values(['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'update_flag'])
        df['rn'] = df.groupby(['ts_code', 'end_date']).cumcount() + 1
        if 'f_ann_date' in df.columns and 'end_date' in df.columns:
            df['delay_days'] = (df['f_ann_date'] - df['end_date']).dt.days
        
        # 去重策略：每一期保留最近更新的一条
        df_dedup = df.sort_values(
            ['ts_code', 'end_date', 'f_ann_date', 'ann_date', 'update_flag'],
            ascending=[True, True, False, False, False]
        ).drop_duplicates(['ts_code', 'end_date'], keep='first').reset_index(drop=True)
        
        self.logger.info(f"  {table_name}: 去重 {len(df)-len(df_dedup):,} 条")
        return df_dedup

    def _merge_tables(self, data: Dict[str, pd.DataFrame], snapshot_date: str) -> pd.DataFrame:
        """
        合并六张表，添加snapshot_date列
        """
        # 1. 从字典中提取数据
        disclosure_df = data.get('disclosure_date').copy()
        bs_df = data.get('balancesheet').copy()
        inc_df = data.get('income').copy()
        cf_df = data.get('cashflow').copy()
        div_df = data.get('dividend').copy()
        mv_df = data.get('mv').copy()
        
        # 2. 在disclosure_date中增加report_type列，以此为准
        disclosure_df['end_date'] = pd.to_datetime(disclosure_df['end_date'])
        disclosure_df['report_type'] = disclosure_df['end_date'].dt.month.map({
            3: 1, 6: 2, 9: 3, 12: 4
        })
    
        # 3. 合并资产负债表
        bs_df = bs_df.rename(columns={'f_ann_date': 'bs_f_ann_date'})
        result_df = pd.merge(
            disclosure_df,
            bs_df.loc[:, ['ts_code', 'end_date', 'bs_f_ann_date'] + self.bs_content_columns],
            on=['ts_code', 'end_date'],
            how='left'
        )
        result_df['has_bs'] = result_df['bs_f_ann_date'].notna().astype(int)
        
        # 4. 合并利润表
        inc_df = inc_df.rename(columns={'f_ann_date': 'inc_f_ann_date'})
        result_df = pd.merge(
            result_df,
            inc_df.loc[:, ['ts_code', 'end_date', 'inc_f_ann_date'] + self.inc_content_columns],
            on=['ts_code', 'end_date'],
            how='left'
        )
        result_df['has_inc'] = result_df['inc_f_ann_date'].notna().astype(int)
        
        # 5. 合并现金流量表
        cf_df = cf_df.rename(columns={'f_ann_date': 'cf_f_ann_date'})
        result_df = pd.merge(
            result_df,
            cf_df.loc[:, ['ts_code', 'end_date', 'cf_f_ann_date'] + self.cf_content_columns],
            on=['ts_code', 'end_date'],
            how='left'
        )
        result_df['has_cf'] = result_df['cf_f_ann_date'].notna().astype(int)

        # 6. 合并分红表
        result_df['has_div'] = 1
        div_df['end_date'] = pd.to_datetime(div_df['end_date'])
        div_df['ex_date'] = pd.to_datetime(div_df['ex_date'])
        div_df['total_div'] = div_df['cash_div'] * div_df['base_share']
        result_df = pd.merge(
            result_df,
            div_df,
            on=['ts_code', 'end_date'],
            how='left'
        )
        result_df['has_div'] = result_df['ex_date'].notna().astype(int)
        
        # 7. 合并市值表
        result_df['actual_date'] = pd.to_datetime(result_df['actual_date'])
        result_df['month'] = result_df['actual_date'].dt.to_period('M')
        mv_df['trade_date'] = pd.to_datetime(mv_df['trade_date'])
        mv_df['month'] = mv_df['trade_date'].dt.to_period('M')
        result_df = pd.merge(
            result_df,
            mv_df.loc[:,['ts_code', 'month', 'total_share', 'float_share', 'total_mv', 'circ_mv']], 
            on=['ts_code', 'month'],
            how='left'
        )
        result_df = result_df.drop('month', axis=1)

        return result_df
        
    def _process_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """单位转换，缺失值填充，加工衍生列"""
        df = df.sort_values(by=['ts_code', 'end_date'])
        q4_mask = df['report_type']==4
        
        # 1. 三张表单位统一换成万元
        df.loc[:,self.bs_content_columns+self.inc_content_columns+self.cf_content_columns] /= 10000

        # 2. 资产负债表年报/半年报更新的列前值填充，利润表/现金流量表用上一年报填充
        for col in self.mrq_columns:
            df[col] = df.groupby('ts_code')[col].fillna(method='ffill', limit=1)

        for col in self.mry_columns:
            df.loc[~q4_mask, col] = np.nan
            df[col] = df.groupby('ts_code')[col].fillna(method='ffill', limit=3)
            
        # 3. 增加衍生列
        # 资产
        df['cash_assets'] = df['money_cap'].fillna(0) + df['trad_asset'].fillna(0)
        df['quick_receivables'] = df['notes_receiv'].fillna(0) + df['accounts_receiv'].fillna(0) 
        df['quick_assets'] = df['cash_assets'].fillna(0) + df['quick_receivables'].fillna(0)
        df['ppe'] = df['fix_assets'].fillna(0) + df['cip'].fillna(0)
        df['immaterial_assets'] = df['total_assets'].fillna(0) - df['ppe'].fillna(0) - df['total_cur_assets'].fillna(0)
        df['soft_assets'] = df['total_assets'].fillna(0) - df['ppe'].fillna(0) - df['cash_assets'].fillna(0)
        df['total_intangible_assets'] = df['intan_assets'].fillna(0) + df['goodwill'].fillna(0)
        df['total_tangible_assets'] = df['total_assets'].fillna(0) - df['total_intangible_assets'].fillna(0)
        ## 经营性资产，分短期和长期
        df['cur_operating_assets'] = df['quick_receivables'].fillna(0) + df['prepayment'].fillna(0) + df['inventories'].fillna(0) + df['contract_assets'].fillna(0)
        df['lt_operating_assets'] = df['ppe'].fillna(0) + df['use_right_assets'].fillna(0) + df['total_intangible_assets'].fillna(0)
        df['total_operating_assets'] = df['cur_operating_assets'].fillna(0) + df['lt_operating_assets'].fillna(0)
        df['non_operating_assets'] = df['lt_eqt_invest'].fillna(0) + df['oth_illiq_fin_assets'].fillna(0) + df['invest_real_estate'].fillna(0) #所有现金视为经营资产，不计算超额现金
        ## 经营性负债，只有短期
        df['quick_payables'] = df['notes_payable'].fillna(0) + df['acct_payable'].fillna(0)
        df['cur_operating_liab'] = df['quick_payables'].fillna(0) + df['adv_receipts'].fillna(0) + df['payroll_payable'].fillna(0) + \
        df['taxes_payable'].fillna(0) + df['contract_liab'].fillna(0)
        df['non_operating_liab'] = df['estimated_liab'].fillna(0) + df['defer_tax_liab'].fillna(0)
        ## 有息负债，分短期和长期
        df['st_interest_bearing_liab'] = df['st_borr'].fillna(0) + df['trading_fl'].fillna(0) + df['non_cur_liab_due_1y'].fillna(0)
        df['lt_interest_bearing_liab'] = df['lt_borr'].fillna(0) + df['bond_payable'].fillna(0) + df['lease_liab'].fillna(0)
        df['interest_bearing_liab'] = df['st_interest_bearing_liab'].fillna(0) + df['lt_interest_bearing_liab'].fillna(0)
        ## 经营性净资产
        df['net_operating_assets'] = df['total_operating_assets'].fillna(0) - df['cur_operating_liab'].fillna(0)
        df['working_capital'] = df['cur_operating_assets'].fillna(0) - df['cur_operating_liab'].fillna(0)
        ## 投入资本
        df['invested_capital'] = df['total_hldr_eqy_exc_min_int'].fillna(0) + df['interest_bearing_liab'].fillna(0) - df['cash_assets'].fillna(0)
        df['retained_earnings'] = df['undistr_porfit'].fillna(0) + df['surplus_rese'].fillna(0)
        # 利润
        df['gp'] = df['revenue'].fillna(0) - df['oper_cost'].fillna(0)
        df['oper_exp'] =  df['sell_exp'].fillna(0) + df['admin_exp'].fillna(0) + df['rd_exp'].fillna(0) 
        df['oper_cost_exp'] = df['oper_cost'].fillna(0) + df['biz_tax_surchg'].fillna(0) + df['oper_exp'].fillna(0)
        df['non_recurring_items'] = df['non_oper_income'].fillna(0) - df['non_oper_exp'].fillna(0)  + df['invest_income'].fillna(0) + \
        df['fv_value_chg_gain'].fillna(0) + df['assets_impair_loss'].fillna(0) + df['credit_impa_loss'].fillna(0) + df['asset_disp_income'].fillna(0) 
        df['core_pretax_profit'] = df['total_profit'].fillna(0) - df['non_recurring_items'].fillna(0)
        # 现金流
        df['da'] = df['depr_fa_coga_dpba'].fillna(0) + df['amort_intang_assets'].fillna(0) + df['lt_amort_deferred_exp'].fillna(0)
        df['working_capital_chg'] = -df['decr_inventories'].fillna(0) - df['decr_oper_payable'].fillna(0) + df['incr_oper_payable'].fillna(0)
        df['net_capex'] = df['c_pay_acq_const_fiolta'].fillna(0) - df['n_recp_disp_fiolta'].fillna(0)
        df['fcf'] = df['n_cashflow_act'].fillna(0) - df['c_pay_acq_const_fiolta'].fillna(0)
        #df['fcfe'] = df['fcff'] - df['c_prepay_amt_borr'].fillna(0) + df['c_recp_borrow'].fillna(0) + df['proc_issue_bonds'].fillna(0)
        
        # 增加跨表计算列
        df['ev'] = df['total_mv'] + df['interest_bearing_liab'].fillna(0) - df['cash_assets'].fillna(0)
        df['evnoa'] = df['ev'] - df['non_operating_assets'] - df['non_operating_liab']

        return df

    def calculate_quarterly_values(self, df: pd.DataFrame, columns_to_process: List[str]) -> pd.DataFrame:
        """计算当季值"""
        result_df = df.copy()
        
        # 转换日期格式
        result_df['end_date'] = pd.to_datetime(result_df['end_date'])
        result_df['year'] = result_df['end_date'].dt.year
        q1_mask = result_df['report_type']==1
        
        for col in columns_to_process:
            result_df[f'{col}_q'] = result_df[col] - result_df.groupby(['ts_code','year'])[col].shift(1)
            result_df.loc[q1_mask, f'{col}_q'] = result_df.loc[q1_mask, col]

        return result_df

    def incremental_update(
        self,
        snapshot_date: str,
        auto_save: bool = True,
        entity_list: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        财务数据增量更新，只接受一个snapshot_date参数
        
        参数:
        snapshot_date: 观察日期 (yyyymmdd格式)
        auto_save: 是否自动保存结果，默认为True
        entity_list: 股票代码列表
        **kwargs: 其他参数
        
        返回:
        处理后的DataFrame
        """
        self.logger.info(f"财务数据增量更新: {snapshot_date}")
        
        # 1. 调用子类自己的get_data方法获取数据
        # 注意：子类的get_data期望snapshot_date是yyyymmdd格式
        data = self.get_data(
            snapshot_date=snapshot_date,
            entity_list=entity_list,
            **kwargs
        )
        
        if not data or data.get('disclosure_date', pd.DataFrame()).empty:
            self.logger.error(f"获取{snapshot_date}数据失败")
            return pd.DataFrame()
        
        # 2. 调用子类自己的process_data方法处理数据
        result = self.process_data(
            data=data,
            snapshot_date=snapshot_date,
            **kwargs
        )
        
        if result.empty:
            self.logger.error(f"处理{snapshot_date}数据失败")
            return pd.DataFrame()
        
        # 4. 自动保存到数据库
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
            
            # 然后使用append模式插入新数据
            write_mode = 'append'
        
        # 使用database.py中的save_to_database函数
        success = save_to_database(data, table_name, write_mode, engine=self.engine)
        
        if success:
            self.logger.info(f"数据已保存到 {table_name}，共 {len(data)} 条记录，写入模式: {write_mode}")
        else:
            self.logger.error(f"数据保存到 {table_name} 失败，写入模式: {write_mode}")