"""个股×日 行情宽表 Panel（从 data/sql/stock_daily_wide.py 迁移）。

表名：panel_stock_daily（基类自动加 panel_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖（schedule_compute.json）：
- daily / adj_factor / daily_basic / stock_st / index_member_all
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class StockDailyPanelCalculator(PanelCalculator):
    """个股×日 行情宽表（行情+复权+市值+ST+行业+指数成分）。"""

    # ===== PanelCalculator 类属性 =====
    table_name = "stock_daily"  # 基类自动加 panel_ 前缀 → panel_stock_daily
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"
    output_schema = {
        "ts_code": "string", "trade_date": "string",
        "open": "float", "high": "float", "low": "float", "close": "float",
        "pre_close": "float", "change": "float", "pct_chg": "float",
        "vol": "float", "amount": "float",
        "log_return": "float", "vwap": "float",
        "adj_factor": "float",
        "is_st": "int", "is_suspend": "int",
        "turnover_rate": "float", "turnover_rate_f": "float", "volume_ratio": "float",
        "pe": "float", "pe_ttm": "float", "pb": "float", "ps": "float", "ps_ttm": "float",
        "dv_ratio": "float", "dv_ttm": "float",
        "total_share": "float", "float_share": "float", "free_share": "float",
        "total_mv": "float", "circ_mv": "float",
        "buy_sm_vol": "float", "buy_md_vol": "float", "buy_lg_vol": "float", "buy_elg_vol": "float",
        "sell_sm_vol": "float", "sell_md_vol": "float", "sell_lg_vol": "float", "sell_elg_vol": "float",
        "buy_sm_amount": "float", "buy_md_amount": "float", "buy_lg_amount": "float", "buy_elg_amount": "float",
        "sell_sm_amount": "float", "sell_md_amount": "float", "sell_lg_amount": "float", "sell_elg_amount": "float",
        "net_mf_amount": "float",
        "market": "string", "exchange": "string", "list_status": "string",
        "list_date": "string", "delist_date": "string", "is_hs": "string",
        "list_days": "int",
        "l1_code": "string", "l1_name": "string", "l2_code": "string", "l2_name": "string",
        "is_hs300": "int", "is_zz500": "int", "is_zz800": "int",
        "is_zz1000": "int", "is_zz2000": "int", "is_zzhl": "int", "is_hldb": "int",
    }

    def __init__(self, engine=None, index_lookback_window: int = 40):
        """初始化。

        Args:
            engine: 数据库引擎，None 用全局
            index_lookback_window: 指数成分回看窗口（月度表，取 40 天保证取到本月+前一个月）
        """
        super().__init__(engine=engine)
        self.index_lookback_window = index_lookback_window
        self.logger.info(f"StockDailyPanelCalculator 初始化，指数回看窗口: {index_lookback_window} 天")

    # ===== get_data：取基础日线 =====
    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        """取 stock_daily 基础数据（按 trade_date 区间）。"""
        query = "SELECT * FROM stock_daily WHERE 1=1"
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        entity_list: Optional[List[str]] = params.get("entity_list")
        if entity_list:
            codes_str = ",".join([f"'{c}'" for c in entity_list])
            query += f" AND ts_code IN ({codes_str})"
        query += " ORDER BY ts_code, trade_date"
        self.logger.info(
            f"取 stock_daily: {start_date or '开始'}~{end_date or '结束'}, "
            f"股票数: {len(entity_list) if entity_list else '全部'}"
        )
        return pd.read_sql(query, self.engine)

    # ===== process_data：join 多表 =====
    def process_data(
        self, data: pd.DataFrame, **params: Any
    ) -> pd.DataFrame:
        """加工为宽表（join 复权/ST/停牌/每日指标/资金流/股票基本信息/指数成分/指数权重）。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return data

        start_date = params.get("start_date")
        end_date = params.get("end_date")
        self.logger.info(f"开始处理股票日线宽表，输入 {len(data)} 条")

        result = self._process_daily_data(data)
        result = self._join_adj_factor(result, start_date, end_date)
        result = self._join_st_info(result, start_date, end_date)
        result = self._join_suspend_info(result, start_date, end_date)
        result = self._join_daily_basic(result, start_date, end_date)
        result = self._join_moneyflow(result, start_date, end_date)
        result = self._join_stock_basic(result)
        result = self._join_index_member(result)
        result = self._join_index_weight(result, start_date, end_date)

        self.logger.info(f"股票日线宽表处理完成，输出 {len(result)} 条")
        result = result.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        return result

    # ===== 内部 join 方法（从 data/sql/stock_daily_wide.py 原样保留） =====
    def _process_daily_data(self, daily_data: pd.DataFrame) -> pd.DataFrame:
        result = daily_data.copy()
        result['amount'] = result['amount'] / 10  # 千元 → 万元
        result['log_return'] = np.log(1 + result['pct_chg'] / 100)
        result['vwap'] = result['amount'] * 10000 / (result['vol'] * 100)  # 万元/(手×100)
        return result

    def _join_adj_factor(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        query = "SELECT ts_code, trade_date, adj_factor FROM adj_factor WHERE 1=1"
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        adj = pd.read_sql(query, self.engine)
        if not adj.empty:
            data = data.merge(adj, on=['ts_code', 'trade_date'], how='left')
        else:
            data['adj_factor'] = 1.0
        return data

    def _join_st_info(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        query = """
        SELECT ts_code, trade_date,
               CASE WHEN type IS NOT NULL THEN 1 ELSE 0 END as is_st
        FROM stock_st WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        st = pd.read_sql(query, self.engine)
        if not st.empty:
            data = data.merge(st, on=['ts_code', 'trade_date'], how='left')
        else:
            data['is_st'] = 0
        return data

    def _join_suspend_info(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        query = """
        SELECT ts_code, trade_date,
               MAX(CASE WHEN suspend_type='S' THEN 1 ELSE 0 END) as is_suspend
        FROM suspend WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        query += " GROUP BY ts_code, trade_date"
        susp = pd.read_sql(query, self.engine)
        if not susp.empty:
            data = data.merge(susp, on=['ts_code', 'trade_date'], how='left')
        else:
            data['is_suspend'] = 0
        return data

    def _join_daily_basic(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        query = """
        SELECT ts_code, trade_date, turnover_rate, turnover_rate_f, volume_ratio,
               pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_share,
               float_share, free_share, total_mv, circ_mv
        FROM stock_daily_basic WHERE 1=1
        """
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        db = pd.read_sql(query, self.engine)
        if not db.empty:
            data = data.merge(db, on=['ts_code', 'trade_date'], how='left')
        return data

    def _join_moneyflow(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        query = "SELECT * FROM moneyflow WHERE 1=1"
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        mf = pd.read_sql(query, self.engine)
        if not mf.empty:
            existing = set(data.columns)
            new_cols = [c for c in mf.columns if c not in existing or c in ['ts_code', 'trade_date']]
            mf = mf[new_cols]
            data = data.merge(mf, on=['ts_code', 'trade_date'], how='left')
        return data

    def _join_stock_basic(self, data: pd.DataFrame) -> pd.DataFrame:
        query = """
        SELECT ts_code, market, exchange, list_status, list_date, delist_date, is_hs
        FROM stock_basic
        """
        sb = pd.read_sql(query, self.engine)
        if not sb.empty:
            data = data.merge(sb, on='ts_code', how='left')
            if 'list_date' in data.columns and 'trade_date' in data.columns:
                data['list_days'] = (
                    pd.to_datetime(data['trade_date']) - pd.to_datetime(data['list_date'])
                ).dt.days
        return data

    def _join_index_member(self, data: pd.DataFrame) -> pd.DataFrame:
        query = "SELECT l1_code, l1_name, l2_code, l2_name, ts_code, in_date FROM index_member_all"
        im = pd.read_sql(query, self.engine)
        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        im['in_date_dt'] = pd.to_datetime(im['in_date'])
        im_sorted = im.sort_values(['in_date_dt'])
        data_sorted = data.sort_values(['trade_date_dt'])
        try:
            merged = pd.merge_asof(
                data_sorted, im_sorted,
                left_on='trade_date_dt', right_on='in_date_dt',
                by='ts_code', direction='backward',
            )
            merged = merged[['ts_code', 'trade_date_dt', 'l1_code', 'l1_name', 'l2_code', 'l2_name']]
            data = data.merge(merged, on=['ts_code', 'trade_date_dt'], how='left')
        except Exception as e:
            self.logger.error(f"merge_asof 行业信息失败: {e}")
        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
        return data

    def _join_index_weight(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        target_indexes = {
            'is_hs300': '399300.SZ', 'is_zz500': '000905.SH', 'is_zz800': '000906.SH',
            'is_zz1000': '000852.SH', 'is_zz2000': '932000.CSI',
            'is_zzhl': '000922.CSI', 'is_hldb': '930955.CSI',
        }
        for col in target_indexes.keys():
            data[col] = 0

        index_codes = list(target_indexes.values())
        codes_str = ",".join([f"'{c}'" for c in index_codes])
        query = f"SELECT index_code, con_code, trade_date FROM index_weight WHERE index_code IN ({codes_str})"

        if start_date:
            sd = start_date.replace('-', '')
            sd_dt = datetime.strptime(sd, '%Y%m%d')
            index_start = (sd_dt - timedelta(days=self.index_lookback_window)).strftime('%Y%m%d')
            query += f" AND trade_date >= '{index_start}'"
        if end_date:
            ed = end_date.replace('-', '') if isinstance(end_date, str) else end_date
            query += f" AND trade_date <= '{ed}'"
        query += " ORDER BY index_code, trade_date, con_code"

        iw = pd.read_sql(query, self.engine)
        if iw.empty:
            return data

        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        iw['trade_date_dt'] = pd.to_datetime(iw['trade_date'])

        for target_col, index_code in target_indexes.items():
            idx_data = iw[iw['index_code'] == index_code].copy()
            if idx_data.empty:
                continue
            adj_dates = sorted(idx_data['trade_date_dt'].unique())
            all_stocks = data['ts_code'].unique()
            stocks_df = pd.DataFrame({'ts_code': all_stocks})
            stocks_df['key'] = 1
            dates_df = pd.DataFrame({'adjustment_date': adj_dates})
            dates_df['key'] = 1
            cart = stocks_df.merge(dates_df, on='key').drop('key', axis=1)
            idx_renamed = idx_data[['con_code', 'trade_date_dt']].rename(
                columns={'con_code': 'ts_code', 'trade_date_dt': 'adjustment_date'}
            )
            idx_renamed['is_component'] = 1
            temp = cart.merge(idx_renamed, on=['ts_code', 'adjustment_date'], how='left')
            temp['is_component'] = temp['is_component'].fillna(0)
            data_sorted = data.sort_values(['trade_date_dt']).copy()
            temp_sorted = temp.sort_values(['adjustment_date']).copy()
            try:
                merged = pd.merge_asof(
                    data_sorted, temp_sorted,
                    left_on='trade_date_dt', right_on='adjustment_date',
                    by='ts_code', direction='backward',
                )
                data = data.merge(
                    merged[['ts_code', 'trade_date_dt', 'is_component']],
                    on=['ts_code', 'trade_date_dt'], how='left',
                )
                data[target_col] = data['is_component'].fillna(0).astype(int)
                data.drop('is_component', axis=1, inplace=True)
            except Exception as e:
                self.logger.error(f"merge_asof 指数 {index_code} 失败: {e}")

        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
        return data
