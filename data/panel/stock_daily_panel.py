"""个股×日 行情宽表 Panel（行业 + 指数成分归属 + 换手率/估值/资金流统一底座）。

表名：panel_stock_daily（基类自动加 panel_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：upsert（按主键覆盖，幂等）

依赖（schedule_compute.json）：
- daily / adj_factor / daily_basic / stock_st / index_member_all / moneyflow / stock_basic / index_weight
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
        "is_sz50": "int", "is_hs300": "int", "is_zz500": "int", "is_zz800": "int",
        "is_zz1000": "int", "is_zz2000": "int", "is_hldb": "int",
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
        """申万行业分类（merge_asof on in_date + out_date 过滤）。

        个股在 tushare 可能多次变更行业（in_date/out_date 对）。
        merge_asof backward 取 trade_date 之前最近一次 in_date，
        但如果该记录的 out_date <= trade_date（已离开该行业），则清空为 None。
        """
        query = (
            "SELECT l1_code, l1_name, l2_code, l2_name, ts_code, in_date, out_date "
            "FROM index_member_all"
        )
        im = pd.read_sql(query, self.engine)
        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        im['in_date_dt'] = pd.to_datetime(im['in_date'])
        im['out_date_dt'] = pd.to_datetime(im['out_date'], errors='coerce')
        im_sorted = im.sort_values(['ts_code', 'in_date_dt'])
        data_sorted = data.sort_values(['ts_code', 'trade_date_dt'])
        try:
            merged = pd.merge_asof(
                data_sorted, im_sorted,
                left_on='trade_date_dt', right_on='in_date_dt',
                by='ts_code', direction='backward',
            )
            # 过滤已离开行业：out_date 非空且 <= trade_date → 行业已失效
            out_mask = merged['out_date_dt'].notna() & (merged['trade_date_dt'] >= merged['out_date_dt'])
            merged.loc[out_mask, ['l1_code', 'l1_name', 'l2_code', 'l2_name']] = None
            merged = merged[['ts_code', 'trade_date_dt', 'l1_code', 'l1_name', 'l2_code', 'l2_name']]
            data = data.merge(merged, on=['ts_code', 'trade_date_dt'], how='left')
        except Exception as e:
            self.logger.error(f"merge_asof 行业信息失败: {e}")
        return data

    def _join_index_weight(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        """指数成分归属（从 panel_index_membership_monthly pivot + 单次 merge_asof）。

        月频成分表已双版归一、月末网格对齐、前向填充，直接 pivot 后 forward-fill 到日。
        """
        target_indexes = {
            'is_sz50': '000016.SH',
            'is_hs300': '000300.SH', 'is_zz500': '000905.SH', 'is_zz800': '000906.SH',
            'is_zz1000': '000852.SH', 'is_zz2000': '932000.CSI',
            'is_hldb': '930955.CSI',
        }
        all_cols = list(target_indexes.keys())
        for col in all_cols:
            data[col] = 0

        index_codes = list(target_indexes.values())
        codes_str = ",".join([f"'{c}'" for c in index_codes])
        query = (
            f"SELECT trade_date, ts_code, index_code FROM panel_index_membership_monthly "
            f"WHERE index_code IN ({codes_str})"
        )
        # 往前多读 2 个月保证 merge_asof 能取到区间首日之前的最近月末快照
        if start_date:
            sd = start_date.replace('-', '')
            sd_dt = datetime.strptime(sd, '%Y%m%d')
            read_start = (sd_dt - timedelta(days=70)).strftime('%Y-%m-%d')
            query += f" AND trade_date >= '{read_start}'"
        if end_date:
            ed = end_date.replace('-', '') if isinstance(end_date, str) else end_date
            ed_dt = datetime.strptime(ed, '%Y%m%d').strftime('%Y-%m-%d')
            query += f" AND trade_date <= '{ed_dt}'"

        mem = pd.read_sql(query, self.engine)
        if mem.empty:
            return data

        mem['trade_date_dt'] = pd.to_datetime(mem['trade_date'])
        mem['_present'] = 1

        # pivot: (ts_code, trade_date_dt) × canonical_index → is_member flag
        membership = mem.pivot_table(
            index=['ts_code', 'trade_date_dt'],
            columns='index_code',
            values='_present',
            fill_value=0,
        ).reset_index()
        membership.rename(columns={v: k for k, v in target_indexes.items()}, inplace=True)
        for c in all_cols:
            if c not in membership.columns:
                membership[c] = 0

        # 单次 merge_asof: 往前找最近一个月末快照，forward-fill 到日
        if 'trade_date_dt' not in data.columns:
            data['trade_date_dt'] = pd.to_datetime(data['trade_date'])

        merged = pd.merge_asof(
            data[['ts_code', 'trade_date_dt']].sort_values(['ts_code', 'trade_date_dt']),
            membership.sort_values(['ts_code', 'trade_date_dt']),
            on='trade_date_dt',
            by='ts_code',
            direction='backward',
        )
        for c in all_cols:
            if c in merged.columns:
                data = data.drop(c, axis=1, errors='ignore')
        data = data.merge(merged[['ts_code', 'trade_date_dt'] + all_cols],
                          on=['ts_code', 'trade_date_dt'], how='left')
        for c in all_cols:
            data[c] = data[c].fillna(0).astype(int)

        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
        return data
