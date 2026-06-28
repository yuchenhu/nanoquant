"""个股×报告期 财报三表快照 Panel（从 data/sql/financial_statements_snapshot.py 迁移）。

表名：panel_financial_statements_snapshot
主键：snapshot_date + ts_code + end_date
biz_date_col：snapshot_date（财务快照日期，非 trade_date）
write_mode：upsert
依赖：disclosure_date / balancesheet / income / cashflow / dividend / panel_mv_monthly

注：本计算器 get_data 返回 dict（多张表），process_data 接受 dict，故重写 update 方法。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config.database import save_to_database
from core.dates import get_today_str
from core.schema import convert_date_columns, ensure_table, infer_schema_from_df
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class FinancialStatementsSnapshotCalculator(PanelCalculator):
    """财报三表快照（disclosure_date + bs/inc/cf + dividend + mv → 派生指标）。"""

    table_name = "financial_statements_snapshot"  # → panel_financial_statements_snapshot
    primary_keys = ["snapshot_date", "ts_code", "end_date"]
    biz_date_col = "snapshot_date"
    write_mode = "upsert"

    def __init__(self, engine=None, lookback_years: int = 4):
        super().__init__(engine=engine)
        self.lookback_years = lookback_years
        self._init_column_lists()
        self.logger.info(f"FinancialStatementsSnapshotCalculator 初始化，回溯 {lookback_years} 年")

    def _init_column_lists(self) -> None:
        self.id_columns = ['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'update_flag']
        self.bs_content_columns = [
            'cap_rese', 'undistr_porfit', 'surplus_rese',
            'money_cap', 'trad_asset', 'notes_receiv', 'accounts_receiv', 'prepayment', 'oth_receiv',
            'inventories', 'contract_assets', 'nca_within_1y', 'oth_cur_assets', 'total_cur_assets',
            'lt_rec', 'lt_eqt_invest', 'oth_illiq_fin_assets', 'invest_real_estate',
            'fix_assets', 'cip', 'use_right_assets',
            'intan_assets', 'r_and_d', 'goodwill', 'lt_amor_exp', 'defer_tax_assets',
            'oth_nca', 'total_nca', 'total_assets',
            'st_borr', 'trading_fl',
            'notes_payable', 'acct_payable', 'adv_receipts', 'contract_liab', 'payroll_payable', 'taxes_payable', 'oth_payable',
            'non_cur_liab_due_1y', 'oth_cur_liab', 'total_cur_liab',
            'lt_borr', 'bond_payable', 'lease_liab', 'estimated_liab', 'defer_tax_liab',
            'oth_ncl', 'total_ncl', 'total_liab',
            'minority_int', 'total_hldr_eqy_exc_min_int', 'total_hldr_eqy_inc_min_int', 'total_liab_hldr_eqy',
        ]
        self.inc_content_columns = [
            'total_revenue', 'revenue',
            'total_cogs', 'oper_cost', 'biz_tax_surchg', 'sell_exp', 'admin_exp', 'rd_exp',
            'fin_exp', 'fin_exp_int_exp', 'fin_exp_int_inc',
            'oth_income', 'invest_income', 'ass_invest_income', 'fv_value_chg_gain',
            'assets_impair_loss', 'credit_impa_loss', 'asset_disp_income',
            'operate_profit', 'non_oper_income', 'non_oper_exp',
            'total_profit', 'income_tax', 'n_income', 'n_income_attr_p', 'minority_gain',
            'oth_compr_income', 't_compr_income', 'compr_inc_attr_p',
            'ebit', 'ebitda',
        ]
        self.cf_content_columns = [
            'net_profit', 'finan_exp', 'c_fr_sale_sg', 'recp_tax_rends', 'c_fr_oth_operate_a', 'c_inf_fr_operate_a',
            'c_paid_goods_s', 'c_paid_to_for_empl', 'c_paid_for_taxes', 'oth_cash_pay_oper_act', 'st_cash_out_act', 'n_cashflow_act',
            'oth_recp_ral_inv_act', 'c_disp_withdrwl_invest', 'c_recp_return_invest', 'n_recp_disp_fiolta', 'n_recp_disp_sobu',
            'stot_inflows_inv_act', 'c_pay_acq_const_fiolta', 'c_paid_invest', 'n_disp_subs_oth_biz', 'oth_pay_ral_inv_act',
            'stot_out_inv_act', 'n_cashflow_inv_act',
            'c_recp_borrow', 'c_recp_cap_contrib', 'incl_cash_rec_saims', 'oth_cash_recp_ral_fnc_act', 'stot_cash_in_fnc_act',
            'free_cashflow', 'c_prepay_amt_borr', 'c_pay_dist_dpcp_int_exp', 'incl_dvd_profit_paid_sc_ms',
            'oth_cashpay_ral_fnc_act', 'stot_cashout_fnc_act', 'n_cash_flows_fnc_act',
            'eff_fx_flu_cash', 'n_incr_cash_cash_equ', 'c_cash_equ_beg_period', 'c_cash_equ_end_period',
            'depr_fa_coga_dpba', 'amort_intang_assets', 'lt_amort_deferred_exp', 'decr_inventories', 'decr_oper_payable', 'incr_oper_payable',
        ]
        self.mrq_columns = ['oth_receiv', 'fix_assets', 'cip', 'oth_payable']
        self.mry_columns = [
            'ebitda', 'net_profit', 'finan_exp', 'depr_fa_coga_dpba', 'amort_intang_assets', 'lt_amort_deferred_exp',
            'decr_inventories', 'decr_oper_payable', 'incr_oper_payable',
        ]

        # 派生列（_process_columns 产出，全为数值）
        self.derived_columns = [
            'cash_assets', 'quick_receivables', 'quick_assets', 'ppe',
            'immaterial_assets', 'soft_assets',
            'total_intangible_assets', 'total_tangible_assets',
            'cur_operating_assets', 'lt_operating_assets', 'total_operating_assets', 'non_operating_assets',
            'quick_payables', 'cur_operating_liab', 'non_operating_liab',
            'st_interest_bearing_liab', 'lt_interest_bearing_liab', 'interest_bearing_liab',
            'net_operating_assets', 'working_capital', 'invested_capital', 'retained_earnings',
            'gp', 'oper_exp', 'oper_cost_exp', 'non_recurring_items', 'core_pretax_profit',
            'da', 'working_capital_chg', 'net_capex', 'fcf',
            'ev', 'evnoa',
        ]

        # 构建 output_schema（显式，避免 NULL 首行导致推断错误）
        self.output_schema = {}
        # 主键 / 日期 / 标记
        self.output_schema.update({
            "snapshot_date": "string", "ts_code": "string",
            "ann_date": "string", "end_date": "string", "pre_date": "string",
            "actual_date": "string", "modify_date": "string",
            "report_type": "int",
            "has_bs": "int", "has_inc": "int", "has_cf": "int", "has_div": "int",
            "bs_f_ann_date": "string", "inc_f_ann_date": "string", "cf_f_ann_date": "string",
            "ex_date": "string",
        })
        # MV / 分红
        self.output_schema.update({
            "total_share": "float", "float_share": "float", "total_mv": "float", "circ_mv": "float",
            "ev": "float", "evnoa": "float",
            "cash_div": "float", "base_share": "float", "total_div": "float",
        })
        # 三表内容列（全数值）
        for c in self.bs_content_columns + self.inc_content_columns + self.cf_content_columns:
            self.output_schema[c] = "float"
        # 派生列
        for c in self.derived_columns:
            self.output_schema[c] = "float"

    # ===== 重写 update：get_data 返回 dict，process_data 接受 dict =====
    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """财务快照按 snapshot_date 逐日跑。

        start_date/end_date 是 snapshot_date 区间（yyyymmdd）。
        """
        start_date = self._normalize_date(start_date) or self._next_after_biz_date() or get_today_str()
        end_date = self._normalize_date(end_date) or get_today_str()

        self.logger.info(f"{self.table_name} update: snapshot_date [{start_date}, {end_date}]")

        # 逐 snapshot_date 跑（财务快照天然按日离散）
        sd_dt = datetime.strptime(start_date, "%Y%m%d")
        ed_dt = datetime.strptime(end_date, "%Y%m%d")
        all_results: List[pd.DataFrame] = []
        cur = sd_dt
        while cur <= ed_dt:
            snap = cur.strftime("%Y%m%d")
            try:
                raw = self.get_data(snap, snap, **params)
                if not raw or raw.get('disclosure_date', pd.DataFrame()).empty:
                    self.logger.warning(f"{self.table_name} snapshot={snap} get_data 空，跳过")
                    cur += timedelta(days=1)
                    continue
                result = self.process_data(raw, snapshot_date=snap, **params)
                if result is not None and not result.empty:
                    all_results.append(result)
                    self.logger.info(f"{self.table_name} snapshot={snap} 处理 {len(result)} 行")
            except Exception as e:
                self.logger.error(f"{self.table_name} snapshot={snap} 失败: {e}")
            cur += timedelta(days=1)

        if not all_results:
            return pd.DataFrame()
        final = pd.concat(all_results, ignore_index=True)
        self.save_to_database(final)
        if self.biz_date_col and self.biz_date_col in final.columns:
            max_biz = self._max_biz_date(final)
            if max_biz:
                self._set_biz_date(max_biz, len(final))
        return final

    # ===== get_data：返回 dict（六张表） =====
    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> Dict[str, pd.DataFrame]:
        snapshot_date = start_date  # 本计算器按 snapshot_date 逐日
        if not snapshot_date:
            return {}
        snapshot_dt = pd.to_datetime(snapshot_date, format='%Y%m%d')
        start = (snapshot_dt - pd.DateOffset(years=self.lookback_years)).strftime('%Y%m%d')
        end = snapshot_dt.strftime('%Y%m%d')
        entity_list: Optional[List[str]] = params.get("entity_list")

        queries = {
            'disclosure_date': f"""
                SELECT * FROM disclosure_date
                WHERE end_date >= '{start}' AND end_date <= '{end}'
                AND (actual_date <= '{end}' OR modify_date <= '{end}')
            """,
            'balancesheet': f"""
                SELECT * FROM balancesheet
                WHERE end_date >= '{start}' AND end_date <= '{end}' AND f_ann_date <= '{end}'
            """,
            'income': f"""
                SELECT * FROM income
                WHERE end_date >= '{start}' AND end_date <= '{end}' AND f_ann_date <= '{end}'
            """,
            'cashflow': f"""
                SELECT * FROM cashflow
                WHERE end_date >= '{start}' AND end_date <= '{end}' AND f_ann_date <= '{end}'
            """,
            'dividend': f"""
                SELECT t.ts_code, t.end_date, t.ex_date, t.cash_div, t.base_share FROM (
                SELECT ts_code, end_date, ex_date, cash_div, base_share,
                ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY update_flag DESC) rn
                FROM dividend
                WHERE end_date >= '{start}' AND end_date <= '{end}' AND ex_date <= '{end}'
                AND div_proc='实施' AND cash_div>0
                ) t WHERE t.rn=1
            """,
            'mv': f"""
                SELECT * FROM panel_mv_monthly
                WHERE trade_date >= '{start}' AND trade_date <= '{end}'
            """,
        }
        data: Dict[str, pd.DataFrame] = {}
        for name, q in queries.items():
            if entity_list:
                codes_str = ",".join([f"'{c}'" for c in entity_list])
                q += f" AND ts_code IN ({codes_str})"
            try:
                data[name] = pd.read_sql(q, self.engine)
            except Exception as e:
                self.logger.error(f"读取 {name} 失败: {e}")
                data[name] = pd.DataFrame()
        return data

    # ===== process_data：接受 dict，返回 DataFrame =====
    def process_data(self, data: Dict[str, pd.DataFrame], **params: Any) -> pd.DataFrame:
        snapshot_date = params.get("snapshot_date")
        if not data or data['disclosure_date'].empty:
            return pd.DataFrame()
        data['disclosure_date'] = self._expand_financial_dates(data['disclosure_date'])
        data['balancesheet'] = data['balancesheet'].loc[:, self.id_columns + self.bs_content_columns]
        data['income'] = data['income'].loc[:, self.id_columns + self.inc_content_columns]
        data['cashflow'] = data['cashflow'].loc[:, self.id_columns + self.cf_content_columns]
        for t in ['balancesheet', 'income', 'cashflow']:
            if not data[t].empty:
                data[t] = self._deduplicate_table(data[t], t)
        merged = self._merge_tables(data, snapshot_date)
        merged = self._process_columns(merged)
        merged['snapshot_date'] = pd.to_datetime(snapshot_date)
        all_cols = list(merged.columns)
        key_cols = [
            'snapshot_date', 'ts_code', 'ann_date', 'end_date', 'pre_date', 'actual_date', 'modify_date', 'report_type',
            'has_bs', 'has_inc', 'has_cf', 'has_div', 'bs_f_ann_date', 'inc_f_ann_date', 'cf_f_ann_date', 'ex_date',
        ]
        mv_cols = ['total_share', 'float_share', 'total_mv', 'circ_mv', 'ev', 'evnoa']
        div_cols = ['cash_div', 'base_share', 'total_div']
        other_cols = [c for c in all_cols if c not in key_cols + mv_cols + div_cols]
        return merged[key_cols + mv_cols + div_cols + other_cols]

    # ===== 内部方法（从 data/sql/financial_statements_snapshot.py 原样保留） =====
    def _expand_financial_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['end_date'] = pd.to_datetime(df['end_date'])

        def create_range(group):
            min_d = group['end_date'].min()
            max_d = group['end_date'].max()
            dates = pd.date_range(start=min_d, end=max_d, freq='Q')
            return pd.DataFrame({'ts_code': group['ts_code'].iloc[0], 'end_date': dates})

        ranges = df.groupby('ts_code').apply(create_range).reset_index(drop=True)
        return pd.merge(ranges, df, on=['ts_code', 'end_date'], how='left')

    def _deduplicate_table(self, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
        for col in ['end_date', 'f_ann_date', 'ann_date']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        df = df.sort_values(['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'update_flag'])
        df['rn'] = df.groupby(['ts_code', 'end_date']).cumcount() + 1
        if 'f_ann_date' in df.columns and 'end_date' in df.columns:
            df['delay_days'] = (df['f_ann_date'] - df['end_date']).dt.days
        df_dedup = df.sort_values(
            ['ts_code', 'end_date', 'f_ann_date', 'ann_date', 'update_flag'],
            ascending=[True, True, False, False, False],
        ).drop_duplicates(['ts_code', 'end_date'], keep='first').reset_index(drop=True)
        self.logger.info(f"  {table_name}: 去重 {len(df) - len(df_dedup):,} 条")
        return df_dedup

    def _merge_tables(self, data: Dict[str, pd.DataFrame], snapshot_date: str) -> pd.DataFrame:
        disclosure_df = data.get('disclosure_date').copy()
        bs_df = data.get('balancesheet').copy()
        inc_df = data.get('income').copy()
        cf_df = data.get('cashflow').copy()
        div_df = data.get('dividend').copy()
        mv_df = data.get('mv').copy()

        disclosure_df['end_date'] = pd.to_datetime(disclosure_df['end_date'])
        disclosure_df['report_type'] = disclosure_df['end_date'].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})

        bs_df = bs_df.rename(columns={'f_ann_date': 'bs_f_ann_date'})
        result_df = pd.merge(
            disclosure_df,
            bs_df.loc[:, ['ts_code', 'end_date', 'bs_f_ann_date'] + self.bs_content_columns],
            on=['ts_code', 'end_date'], how='left',
        )
        result_df['has_bs'] = result_df['bs_f_ann_date'].notna().astype(int)

        inc_df = inc_df.rename(columns={'f_ann_date': 'inc_f_ann_date'})
        result_df = pd.merge(
            result_df,
            inc_df.loc[:, ['ts_code', 'end_date', 'inc_f_ann_date'] + self.inc_content_columns],
            on=['ts_code', 'end_date'], how='left',
        )
        result_df['has_inc'] = result_df['inc_f_ann_date'].notna().astype(int)

        cf_df = cf_df.rename(columns={'f_ann_date': 'cf_f_ann_date'})
        result_df = pd.merge(
            result_df,
            cf_df.loc[:, ['ts_code', 'end_date', 'cf_f_ann_date'] + self.cf_content_columns],
            on=['ts_code', 'end_date'], how='left',
        )
        result_df['has_cf'] = result_df['cf_f_ann_date'].notna().astype(int)

        result_df['has_div'] = 1
        div_df['end_date'] = pd.to_datetime(div_df['end_date'])
        div_df['ex_date'] = pd.to_datetime(div_df['ex_date'])
        div_df['total_div'] = div_df['cash_div'] * div_df['base_share']
        result_df = pd.merge(result_df, div_df, on=['ts_code', 'end_date'], how='left')
        result_df['has_div'] = result_df['ex_date'].notna().astype(int)

        result_df['actual_date'] = pd.to_datetime(result_df['actual_date'])
        result_df['month'] = result_df['actual_date'].dt.to_period('M')
        mv_df['trade_date'] = pd.to_datetime(mv_df['trade_date'])
        mv_df['month'] = mv_df['trade_date'].dt.to_period('M')
        result_df = pd.merge(
            result_df,
            mv_df.loc[:, ['ts_code', 'month', 'total_share', 'float_share', 'total_mv', 'circ_mv']],
            on=['ts_code', 'month'], how='left',
        )
        result_df = result_df.drop('month', axis=1)
        return result_df

    def _process_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(by=['ts_code', 'end_date'])
        q4_mask = df['report_type'] == 4
        df.loc[:, self.bs_content_columns + self.inc_content_columns + self.cf_content_columns] /= 10000
        for col in self.mrq_columns:
            df[col] = df.groupby('ts_code')[col].fillna(method='ffill', limit=1)
        for col in self.mry_columns:
            df.loc[~q4_mask, col] = np.nan
            df[col] = df.groupby('ts_code')[col].fillna(method='ffill', limit=3)
        # 资产
        df['cash_assets'] = df['money_cap'].fillna(0) + df['trad_asset'].fillna(0)
        df['quick_receivables'] = df['notes_receiv'].fillna(0) + df['accounts_receiv'].fillna(0)
        df['quick_assets'] = df['cash_assets'].fillna(0) + df['quick_receivables'].fillna(0)
        df['ppe'] = df['fix_assets'].fillna(0) + df['cip'].fillna(0)
        df['immaterial_assets'] = df['total_assets'].fillna(0) - df['ppe'].fillna(0) - df['total_cur_assets'].fillna(0)
        df['soft_assets'] = df['total_assets'].fillna(0) - df['ppe'].fillna(0) - df['cash_assets'].fillna(0)
        df['total_intangible_assets'] = df['intan_assets'].fillna(0) + df['goodwill'].fillna(0)
        df['total_tangible_assets'] = df['total_assets'].fillna(0) - df['total_intangible_assets'].fillna(0)
        df['cur_operating_assets'] = df['quick_receivables'].fillna(0) + df['prepayment'].fillna(0) + df['inventories'].fillna(0) + df['contract_assets'].fillna(0)
        df['lt_operating_assets'] = df['ppe'].fillna(0) + df['use_right_assets'].fillna(0) + df['total_intangible_assets'].fillna(0)
        df['total_operating_assets'] = df['cur_operating_assets'].fillna(0) + df['lt_operating_assets'].fillna(0)
        df['non_operating_assets'] = df['lt_eqt_invest'].fillna(0) + df['oth_illiq_fin_assets'].fillna(0) + df['invest_real_estate'].fillna(0)
        df['quick_payables'] = df['notes_payable'].fillna(0) + df['acct_payable'].fillna(0)
        df['cur_operating_liab'] = df['quick_payables'].fillna(0) + df['adv_receipts'].fillna(0) + df['payroll_payable'].fillna(0) + df['taxes_payable'].fillna(0) + df['contract_liab'].fillna(0)
        df['non_operating_liab'] = df['estimated_liab'].fillna(0) + df['defer_tax_liab'].fillna(0)
        df['st_interest_bearing_liab'] = df['st_borr'].fillna(0) + df['trading_fl'].fillna(0) + df['non_cur_liab_due_1y'].fillna(0)
        df['lt_interest_bearing_liab'] = df['lt_borr'].fillna(0) + df['bond_payable'].fillna(0) + df['lease_liab'].fillna(0)
        df['interest_bearing_liab'] = df['st_interest_bearing_liab'].fillna(0) + df['lt_interest_bearing_liab'].fillna(0)
        df['net_operating_assets'] = df['total_operating_assets'].fillna(0) - df['cur_operating_liab'].fillna(0)
        df['working_capital'] = df['cur_operating_assets'].fillna(0) - df['cur_operating_liab'].fillna(0)
        df['invested_capital'] = df['total_hldr_eqy_exc_min_int'].fillna(0) + df['interest_bearing_liab'].fillna(0) - df['cash_assets'].fillna(0)
        df['retained_earnings'] = df['undistr_porfit'].fillna(0) + df['surplus_rese'].fillna(0)
        # 利润
        df['gp'] = df['revenue'].fillna(0) - df['oper_cost'].fillna(0)
        df['oper_exp'] = df['sell_exp'].fillna(0) + df['admin_exp'].fillna(0) + df['rd_exp'].fillna(0)
        df['oper_cost_exp'] = df['oper_cost'].fillna(0) + df['biz_tax_surchg'].fillna(0) + df['oper_exp'].fillna(0)
        df['non_recurring_items'] = (
            df['non_oper_income'].fillna(0) - df['non_oper_exp'].fillna(0) + df['invest_income'].fillna(0)
            + df['fv_value_chg_gain'].fillna(0) + df['assets_impair_loss'].fillna(0)
            + df['credit_impa_loss'].fillna(0) + df['asset_disp_income'].fillna(0)
        )
        df['core_pretax_profit'] = df['total_profit'].fillna(0) - df['non_recurring_items'].fillna(0)
        # 现金流
        df['da'] = df['depr_fa_coga_dpba'].fillna(0) + df['amort_intang_assets'].fillna(0) + df['lt_amort_deferred_exp'].fillna(0)
        df['working_capital_chg'] = -df['decr_inventories'].fillna(0) - df['decr_oper_payable'].fillna(0) + df['incr_oper_payable'].fillna(0)
        df['net_capex'] = df['c_pay_acq_const_fiolta'].fillna(0) - df['n_recp_disp_fiolta'].fillna(0)
        df['fcf'] = df['n_cashflow_act'].fillna(0) - df['c_pay_acq_const_fiolta'].fillna(0)
        # 跨表
        df['ev'] = df['total_mv'] + df['interest_bearing_liab'].fillna(0) - df['cash_assets'].fillna(0)
        df['evnoa'] = df['ev'] - df['non_operating_assets'] - df['non_operating_liab']
        return df
