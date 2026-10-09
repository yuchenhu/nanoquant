"""个股×日 行情宽表 Panel（行业 + 换手率/估值/资金流统一底座）。

表名：panel_stock_daily（基类自动加 panel_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：overwrite（按 trade_date 分区删除+批量写入，幂等）

依赖（schedule_compute.json）：
- daily / adj_factor / daily_basic / stock_st / index_member_all / moneyflow / stock_basic

[设计债清理 2026-10-09，ROADMAP P1.5] 原 8 个 is_xxx 指数成分布尔列已删除：
无法穷尽、池扩展需重写 14M 行宽表、与 panel_index_membership_monthly 单一事实源
重复物化。下游改为直接 join membership 面板（首个消费者 market_sentiment_monthly
已改）。注意 is_st / is_suspend 保留（个股状态，非指数成分）。
"""
from __future__ import annotations

import logging
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
    write_mode = "overwrite"
    partition_col = "trade_date"  # 按交易日覆盖（删除+批量写入，省时间）
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
        "net_mf_vol": "float", "net_mf_amount": "float",
        "market": "string", "exchange": "string", "list_status": "string",
        "list_date": "string", "delist_date": "string", "is_hs": "string",
        "list_days": "int",
        "l1_code": "string", "l1_name": "string", "l2_code": "string", "l2_name": "string",
    }

    def __init__(self, engine=None):
        """初始化。"""
        super().__init__(engine=engine)
        self.logger.info("StockDailyPanelCalculator 初始化")

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
        self.logger.info(f"开始处理股票日线宽表，输入 {len(data)} 条，日期范围: {start_date}~{end_date}")

        import time
        start_time = time.time()

        result = self._process_daily_data(data)
        step_time = time.time()
        self.logger.info(f"[0/8] _process_daily_data 完成，耗时 {step_time - start_time:.1f}s")

        result = self._join_adj_factor(result, start_date, end_date)
        step_time2 = time.time()
        self.logger.info(f"[1/8] _join_adj_factor 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_st_info(result, start_date, end_date)
        step_time2 = time.time()
        self.logger.info(f"[2/8] _join_st_info 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_suspend_info(result, start_date, end_date)
        step_time2 = time.time()
        self.logger.info(f"[3/8] _join_suspend_info 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_daily_basic(result, start_date, end_date)
        step_time2 = time.time()
        self.logger.info(f"[4/8] _join_daily_basic 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_moneyflow(result, start_date, end_date)
        step_time2 = time.time()
        self.logger.info(f"[5/8] _join_moneyflow 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_stock_basic(result)
        step_time2 = time.time()
        self.logger.info(f"[6/8] _join_stock_basic 完成，耗时 {step_time2 - step_time:.1f}s")
        step_time = step_time2

        result = self._join_index_member(result)
        step_time2 = time.time()
        self.logger.info(f"[7/7] _join_index_member 完成，耗时 {step_time2 - step_time:.1f}s")

        total_time = time.time() - start_time
        self.logger.info(f"股票日线宽表处理完成，输出 {len(result)} 条，总耗时 {total_time:.1f}s")
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
        self.logger.info("[1/8] join adj_factor 复权因子...")
        query = "SELECT ts_code, trade_date, adj_factor FROM adj_factor WHERE 1=1"
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        adj = pd.read_sql(query, self.engine)
        self.logger.info(f"  读取 adj_factor: {len(adj)} 行")
        if not adj.empty:
            data = data.merge(adj, on=['ts_code', 'trade_date'], how='left')
        else:
            data['adj_factor'] = 1.0
            self.logger.info("  adj_factor 为空，默认填充 1.0")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_st_info(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        self.logger.info("[2/8] join stock_st ST 状态...")
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
        self.logger.info(f"  读取 stock_st: {len(st)} 行")
        if not st.empty:
            data = data.merge(st, on=['ts_code', 'trade_date'], how='left')
        else:
            data['is_st'] = 0
            self.logger.info("  stock_st 为空，默认填充 0")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_suspend_info(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        self.logger.info("[3/8] join suspend 停牌信息...")
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
        self.logger.info(f"  读取 suspend: {len(susp)} 行")
        if not susp.empty:
            data = data.merge(susp, on=['ts_code', 'trade_date'], how='left')
        else:
            data['is_suspend'] = 0
            self.logger.info("  suspend 为空，默认填充 0")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_daily_basic(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        self.logger.info("[4/8] join stock_daily_basic 每日指标(换手率/估值/市值)...")
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
        self.logger.info(f"  读取 stock_daily_basic: {len(db)} 行")
        if not db.empty:
            data = data.merge(db, on=['ts_code', 'trade_date'], how='left')
        else:
            self.logger.info("  stock_daily_basic 为空")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_moneyflow(self, data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
        self.logger.info("[5/8] join moneyflow 资金流数据...")
        query = "SELECT * FROM moneyflow WHERE 1=1"
        if start_date:
            query += f" AND trade_date >= '{start_date}'"
        if end_date:
            query += f" AND trade_date <= '{end_date}'"
        mf = pd.read_sql(query, self.engine)
        self.logger.info(f"  读取 moneyflow: {len(mf)} 行")
        if not mf.empty:
            existing = set(data.columns)
            new_cols = [c for c in mf.columns if c not in existing or c in ['ts_code', 'trade_date']]
            mf = mf[new_cols]
            data = data.merge(mf, on=['ts_code', 'trade_date'], how='left')
            self.logger.info(f"  join 列数: {len(new_cols)}")
        else:
            self.logger.info("  moneyflow 为空")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_stock_basic(self, data: pd.DataFrame) -> pd.DataFrame:
        self.logger.info("[6/8] join stock_basic 股票基本信息(市场/上市日期)...")
        query = """
        SELECT ts_code, market, exchange, list_status, list_date, delist_date, is_hs
        FROM stock_basic
        """
        sb = pd.read_sql(query, self.engine)
        self.logger.info(f"  读取 stock_basic: {len(sb)} 行")
        if not sb.empty:
            data = data.merge(sb, on='ts_code', how='left')
            if 'list_date' in data.columns and 'trade_date' in data.columns:
                data['list_days'] = (
                    pd.to_datetime(data['trade_date']) - pd.to_datetime(data['list_date'])
                ).dt.days
                self.logger.info("  计算 list_days 完成")
        else:
            self.logger.info("  stock_basic 为空")
        self.logger.info(f"  完成后行数: {len(data)}")
        return data

    def _join_index_member(self, data: pd.DataFrame) -> pd.DataFrame:
        """申万行业分类（merge_asof on in_date + out_date 过滤）。

        个股在 tushare 可能多次变更行业（in_date/out_date 对）。
        merge_asof backward 取 trade_date 之前最近一次 in_date，
        但如果该记录的 out_date <= trade_date（已离开该行业），则清空为 None。
        """
        self.logger.info("[7/8] join index_member_all 申万行业分类(merge_asof)...")
        query = (
            "SELECT l1_code, l1_name, l2_code, l2_name, ts_code, in_date, out_date "
            "FROM index_member_all"
        )
        im = pd.read_sql(query, self.engine)
        self.logger.info(f"  读取 index_member_all: {len(im)} 行")

        if im.empty:
            self.logger.info("  index_member_all 为空，跳过")
            return data

        data['trade_date_dt'] = pd.to_datetime(data['trade_date'])
        im['in_date_dt'] = pd.to_datetime(im['in_date'])
        im['out_date_dt'] = pd.to_datetime(im['out_date'], errors='coerce')
        im_sorted = im.sort_values('in_date_dt').reset_index(drop=True)
        data_sorted = data.sort_values('trade_date_dt').reset_index(drop=True)

        self.logger.info("  开始 merge_asof...")
        try:
            merged = pd.merge_asof(
                data_sorted, im_sorted,
                left_on='trade_date_dt', right_on='in_date_dt',
                by='ts_code', direction='backward',
            )
            self.logger.info(f"  merge_asof 完成: {len(merged)} 行")

            out_mask = merged['out_date_dt'].notna() & (merged['trade_date_dt'] >= merged['out_date_dt'])
            out_count = out_mask.sum()
            self.logger.info(f"  过滤已离开行业记录: {out_count} 条")

            merged.loc[out_mask, ['l1_code', 'l1_name', 'l2_code', 'l2_name']] = None
            merged = merged[['ts_code', 'trade_date_dt', 'l1_code', 'l1_name', 'l2_code', 'l2_name']]
            data = data.merge(merged, on=['ts_code', 'trade_date_dt'], how='left')
        except Exception as e:
            self.logger.error(f"  merge_asof 失败: {e}")

        data.drop('trade_date_dt', axis=1, inplace=True, errors='ignore')
        self.logger.info(f"  完成后行数: {len(data)}")
        return data
