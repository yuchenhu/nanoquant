"""个股×报告期 财务指标快照 Panel（从 data/sql/financial_indicators_snapshot.py 迁移）。

表名：panel_financial_indicators_snapshot
主键：snapshot_date + ts_code + end_date
biz_date_col：snapshot_date（财务快照日期，非 trade_date）
write_mode：upsert
依赖：financial_statements_snapshot（panel_financial_statements_snapshot）
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class FinancialIndicatorsSnapshotCalculator(PanelCalculator):
    """财务指标快照（q/ttm/yoy/估值/盈利/质量 派生指标）。"""

    table_name = "financial_indicators_snapshot"  # → panel_financial_indicators_snapshot
    primary_keys = ["snapshot_date", "ts_code", "end_date"]
    biz_date_col = "snapshot_date"  # 财务快照日期
    write_mode = "upsert"
    # output_schema 在 _init_column_lists 中显式构建，避免 NULL 首行推断错误

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self._init_column_lists()
        self.logger.info("FinancialIndicatorsSnapshotCalculator 初始化")

    def _init_column_lists(self) -> None:
        self.q_cols = [
            'revenue', 'oper_cost', 'gp', 'oper_exp', 'sell_exp', 'admin_exp', 'rd_exp',
            'oper_cost_exp', 'operate_profit', 'total_profit', 'income_tax', 'n_income',
            'fin_exp_int_inc', 'ebit', 'n_cashflow_act', 'n_incr_cash_cash_equ', 'fcf',
        ]
        self.ttm_avg_cols = [
            'cash_assets', 'quick_receivables', 'inventories', 'total_cur_assets', 'ppe',
            'total_intangible_assets', 'immaterial_assets', 'total_assets',
            'quick_payables', 'interest_bearing_liab', 'total_liab',
            'total_hldr_eqy_exc_min_int', 'net_operating_assets',
        ]
        self.valuation_cols = [
            ('bp', 'total_hldr_eqy_exc_min_int', 'total_mv'),
            ('rep', 'retained_earnings', 'total_mv'),
            ('sp_q', 'revenue_q', 'total_mv'), ('gpp_q', 'gp_q', 'total_mv'),
            ('ep_q', 'n_income_q', 'total_mv'),
            ('sellp_q', 'sell_exp_q', 'total_mv'), ('admp_q', 'admin_exp_q', 'total_mv'),
            ('rdp_q', 'rd_exp_q', 'total_mv'), ('taxp_q', 'income_tax_q', 'total_mv'),
            ('ocfp_q', 'n_cashflow_act_q', 'total_mv'), ('ebitp_q', 'ebit_q', 'total_mv'),
            ('ebitdap', 'ebitda', 'total_mv'),
            ('sp_ttm', 'revenue_ttm', 'total_mv'), ('gpp_ttm', 'gp_ttm', 'total_mv'),
            ('ep_ttm', 'n_income_ttm', 'total_mv'),
            ('sellp_ttm', 'sell_exp_ttm', 'total_mv'), ('admp_ttm', 'admin_exp_ttm', 'total_mv'),
            ('rdp_ttm', 'rd_exp_ttm', 'total_mv'), ('taxp_ttm', 'income_tax_ttm', 'total_mv'),
            ('ocfp_ttm', 'n_cashflow_act_ttm', 'total_mv'), ('ebitp_ttm', 'ebit_ttm', 'total_mv'),
            ('divp_ttm', 'total_div_ttm', 'total_mv'),
            ('b2ev', 'total_hldr_eqy_exc_min_int', 'ev'),
            ('re2ev', 'retained_earnings', 'ev'),
            ('s2ev_q', 'revenue_q', 'ev'), ('gp2ev_q', 'gp_q', 'ev'),
            ('e2ev_q', 'n_income_q', 'ev'),
            ('sell2ev_q', 'sell_exp_q', 'ev'), ('adm2ev_q', 'admin_exp_q', 'ev'),
            ('rd2ev_q', 'rd_exp_q', 'ev'), ('tax2ev_q', 'income_tax_q', 'ev'),
            ('ocf2ev_q', 'n_cashflow_act_q', 'ev'), ('ebit2ev_q', 'ebit_q', 'ev'),
            ('ebitda2ev', 'ebitda', 'ev'),
            ('s2ev_ttm', 'revenue_ttm', 'ev'), ('gp2ev_ttm', 'gp_ttm', 'ev'),
            ('e2ev_ttm', 'n_income_ttm', 'ev'),
            ('sell2ev_ttm', 'sell_exp_ttm', 'ev'), ('adm2ev_ttm', 'admin_exp_ttm', 'ev'),
            ('rd2ev_ttm', 'rd_exp_ttm', 'ev'), ('tax2ev_ttm', 'income_tax_ttm', 'ev'),
            ('ocf2ev_ttm', 'n_cashflow_act_ttm', 'ev'), ('ebit2ev_ttm', 'ebit_ttm', 'ev'),
            ('div2ev_ttm', 'total_div_ttm', 'ev'),
            ('noa2evnoa', 'net_operating_assets', 'evnoa'),
            ('s2evnoa_q', 'revenue_q', 'evnoa'), ('gp2evnoa_q', 'gp_q', 'evnoa'),
            ('ocf2evnoa_q', 'n_cashflow_act_q', 'evnoa'),
            ('s2evnoa_ttm', 'revenue_ttm', 'evnoa'), ('gp2evnoa_ttm', 'gp_ttm', 'evnoa'),
            ('ocf2evnoa_ttm', 'n_cashflow_act_ttm', 'evnoa'),
        ]
        self.profit_cols = [
            ('roa_q', 'n_income_q', 'total_assets'),
            ('roe_q', 'n_income_q', 'total_hldr_eqy_exc_min_int'),
            ('ronoa_q', 'operate_profit_q', 'net_operating_assets'),
            ('roic_q', 'ebit_q', 'net_operating_assets'),
            ('gpm_q', 'gp_q', 'revenue_q'), ('npm_q', 'n_income_q', 'revenue_q'),
            ('opexp2sales_q', 'oper_exp_q', 'revenue_q'),
            ('sell2sales_q', 'sell_exp_q', 'revenue_q'),
            ('adm2sales_q', 'admin_exp_q', 'revenue_q'),
            ('rd2sales_q', 'rd_exp_q', 'revenue_q'),
            ('tax2sales_q', 'income_tax_q', 'revenue_q'),
            ('ocf2sales_q', 'n_cashflow_act_q', 'revenue_q'),
            ('np2costexp_q', 'n_income_q', 'oper_cost_exp_q'),
            ('ocf2sales_ttm', 'n_cashflow_act_ttm', 'revenue_ttm'),
            ('ocf2opp_ttm', 'n_cashflow_act_ttm', 'operate_profit_ttm'),
            ('ocf2profit_ttm', 'n_cashflow_act_ttm', 'n_income_ttm'),
            ('fcf2sales_ttm', 'fcf_ttm', 'revenue_ttm'),
            ('fcf2opp_ttm', 'fcf_ttm', 'operate_profit_ttm'),
            ('fcf2profit_ttm', 'fcf_ttm', 'n_income_ttm'),
            ('ncf2sales_ttm', 'n_incr_cash_cash_equ_ttm', 'revenue_ttm'),
            ('ncf2opp_ttm', 'n_incr_cash_cash_equ_ttm', 'operate_profit_ttm'),
            ('ncf2profit_ttm', 'n_incr_cash_cash_equ_ttm', 'n_income_ttm'),
            ('opincome2ebt_q', 'operate_profit_q', 'total_profit_q'),
            ('div2profit_ttm', 'total_div_ttm', 'n_income_ttm'),
            ('div2sales_ttm', 'total_div_ttm', 'revenue_ttm'),
        ]
        self.quality_cols = [
            ('cash2a', 'cash_assets', 'total_assets'),
            ('ar2a', 'quick_receivables', 'total_assets'),
            ('inv2a', 'inventories', 'total_assets'),
            ('ca2a', 'total_cur_assets', 'total_assets'),
            ('ppe2a', 'ppe', 'total_assets'),
            ('cip2a', 'cip', 'total_assets'),
            ('intan2a', 'total_intangible_assets', 'total_assets'),
            ('ima2a', 'immaterial_assets', 'total_assets'),
            ('ap2a', 'quick_payables', 'total_assets'),
            ('ibl2a', 'interest_bearing_liab', 'total_assets'),
            ('d2a', 'total_liab', 'total_assets'),
            ('da2a', 'da', 'total_assets'),
            ('cd2d', 'total_cur_liab', 'total_liab'),
            ('ca2cd', 'total_cur_assets', 'total_cur_liab'),
            ('ibl2cash', 'interest_bearing_liab', 'cash_assets'),
            ('sibl2cash', 'st_interest_bearing_liab', 'cash_assets'),
            ('int2cash', 'fin_exp_int_inc_ttm', 'cash_assets'),
            ('a2e', 'total_assets', 'total_hldr_eqy_exc_min_int'),
            ('ar_tvr_ttm', 'revenue_ttm', 'quick_receivables_ttm_avg'),
            ('inv_tvr_ttm', 'oper_cost_ttm', 'inventories_ttm_avg'),
            ('inv_tvr_ttm2', 'revenue_ttm', 'inventories_ttm_avg'),
            ('ca_tvr_ttm', 'revenue_ttm', 'total_cur_assets_ttm_avg'),
            ('ppe_tvr_ttm', 'revenue_ttm', 'ppe_ttm_avg'),
            ('assets_tvr_ttm', 'revenue_ttm', 'total_assets_ttm_avg'),
            ('ap_tvr_ttm', 'oper_cost_ttm', 'quick_payables_ttm_avg'),
            ('equity_tvr_ttm', 'revenue_ttm', 'total_hldr_eqy_exc_min_int_ttm_avg'),
        ]
        self.multiples = self.valuation_cols + self.profit_cols + self.quality_cols

        # 构建 output_schema（显式，与 _select_and_clean_columns 的列清单一致）
        self.output_schema = {
            "snapshot_date": "string", "ts_code": "string", "end_date": "string",
            "ann_date": "string", "pre_date": "string", "actual_date": "string",
            "modify_date": "string", "report_type": "int",
            "total_mv": "float",
        }
        for col in self.q_cols:
            self.output_schema[f'{col}_q'] = "float"
            self.output_schema[f'{col}_ttm'] = "float"
        for col in self.ttm_avg_cols:
            self.output_schema[col] = "float"
        for output, X, Y in self.multiples:
            self.output_schema[output] = "float"
        for col in self.q_cols + self.ttm_avg_cols:
            self.output_schema[f'{col}_yoy'] = "float"
        for output, X, Y in self.profit_cols + self.quality_cols:
            self.output_schema[f'{output}_yoy'] = "float"

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """取 panel_financial_statements_snapshot（按 snapshot_date 区间）。

        注：本计算器以 snapshot_date 为 biz_date，故 start_date/end_date 对应 snapshot_date 区间。
        """
        query = "SELECT * FROM panel_financial_statements_snapshot WHERE 1=1"
        if start_date:
            query += f" AND snapshot_date >= '{start_date}'"
        if end_date:
            query += f" AND snapshot_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"
        query += " ORDER BY ts_code, end_date"
        return pd.read_sql(query, self.engine)

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data.empty:
            return data
        result = data.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
        result = self._preprocessing(result)
        result = self._calculate_xy_indicators(result)
        result = self._calculate_yoy_indicators(result)
        result = self._select_and_clean_columns(result)
        return result

    def _preprocessing(self, result: pd.DataFrame) -> pd.DataFrame:
        result['end_date'] = pd.to_datetime(result['end_date'])
        result['year'] = result['end_date'].dt.year
        q1_mask = result['report_type'] == 1
        grouped = result.groupby('ts_code')
        for col in self.q_cols:
            result[f'{col}_q'] = grouped[col].diff()
            result.loc[q1_mask, f'{col}_q'] = result.loc[q1_mask, col]
        for col in self.q_cols:
            result[f'{col}_ttm'] = grouped[f'{col}_q'].rolling(4, min_periods=4).sum().reset_index(level=0, drop=True)
        if 'total_div' in result.columns:
            result['total_div_ttm'] = grouped['total_div'].fillna(0).rolling(4, min_periods=1).sum().reset_index(level=0, drop=True)
        for col in self.ttm_avg_cols:
            result[f'{col}_ttm_avg'] = grouped[col].rolling(4, min_periods=1).mean().reset_index(level=0, drop=True)
        return result

    def _calculate_xy_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        for output, X, Y in self.multiples:
            df[output] = self.safe_divide(df, X, Y)
        return df

    def _calculate_yoy_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in self.q_cols:
            df[f'{col}_q'] = pd.to_numeric(df[f'{col}_q'], errors='coerce')
            df[f'{col}_delta4'] = df[f'{col}_q'] - df.groupby('ts_code')[f'{col}_q'].shift(4)
            df[f'{col}_lag4_abs'] = df.groupby('ts_code')[f'{col}_q'].shift(4).abs()
            df[f'{col}_yoy'] = self.safe_divide(df, f'{col}_delta4', f'{col}_lag4_abs', 1e2)
        for col in self.ttm_avg_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            df[f'{col}_delta4'] = df[col] - df.groupby('ts_code')[col].shift(4)
            df[f'{col}_lag4_abs'] = df.groupby('ts_code')[col].shift(4).abs()
            df[f'{col}_yoy'] = self.safe_divide(df, f'{col}_delta4', f'{col}_lag4_abs', 1e2)
        for col, X, Y in self.profit_cols + self.quality_cols:
            df[f'{col}_delta4'] = df[col] - df.groupby('ts_code')[col].shift(4)
            df[f'{col}_lag4_abs'] = df.groupby('ts_code')[col].shift(4).abs()
            df[f'{col}_yoy'] = self.safe_divide(df, f'{col}_delta4', f'{col}_lag4_abs', 1e-4)
        return df

    def _select_and_clean_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        id_cols = [
            'snapshot_date', 'ts_code', 'ann_date', 'end_date',
            'pre_date', 'actual_date', 'modify_date', 'report_type',
        ]
        content_cols = ['total_mv']
        content_cols.extend([f'{col}_q' for col in self.q_cols])
        content_cols.extend([f'{col}_ttm' for col in self.q_cols])
        content_cols.extend(self.ttm_avg_cols)
        content_cols.extend([output for output, X, Y in self.multiples])
        content_cols.extend([f'{col}_yoy' for col in self.q_cols + self.ttm_avg_cols])
        content_cols.extend([f'{output}_yoy' for output, X, Y in self.profit_cols + self.quality_cols])
        existing_content = [c for c in content_cols if c in df.columns]
        existing_id = [c for c in id_cols if c in df.columns]
        result = df[existing_id + existing_content]
        result = result.replace([np.nan, np.inf, -np.inf], None)
        return result

    def safe_divide(self, df: pd.DataFrame, num_col: str, den_col: str, min_threshold: float = 1e2) -> pd.Series:
        if num_col not in df.columns or den_col not in df.columns:
            return pd.Series(np.nan, index=df.index)
        return (df[num_col] / df[den_col]).where(df[den_col].abs() > min_threshold, np.nan)
