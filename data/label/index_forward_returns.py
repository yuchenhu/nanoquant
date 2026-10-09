"""指数前瞻收益标签（宽基 + 风格 + 申万行业 L1，2026-10-09）。

表名：label_index_forward_returns（基类自动加 label_ 前缀）
主键：ts_code + trade_date
biz_date_col：trade_date
write_mode：overwrite（partition_col=trade_date；批 = 区间内完整分区，重跑精确替换）
依赖：index_daily（指数池行情，canonical 归一去双版）、sw_daily（申万 L1 行情）

标签口径（防"信号日收盘成交"的未来函数）：
    ret_fwd_N(T) = close(T+1+N) / close(T+1) - 1
    即 T 日收盘出信号 -> T+1 收盘买入 -> 持有 N 个交易日 -> 收盘卖出。
    N 是该指数自身日序列上的行数偏移（groupby shift）。

持有期为什么按自身序列数、不 reindex 到 trade_cal（业内标准，与
label_forward_returns 个股口径同理）：
    A 股指数不停牌，开市日必有值，自身序列 ≈ 市场日历；差异仅来自源数据缺口。
    reindex + ffill 会伪造价格，reindex + NaN 会让缺口日占位、持有期被拉长，
    都不如直接在自身序列上 shift（缺口日自动顺延一天）。
    缺口是否存在用验收审计量化（见模块底部验收锚点），不靠假设。

增量语义（特殊，覆盖基类默认水位增量）：
    前向 label 要 T+1+120 个交易日才 finalize（最大窗口 120 -> 距 T 为 121），
    且需覆盖 index_daily 近端数据修订，故增量模式 = 固定重刷近
    refresh_trailing_days = max(window)+1+4 = 125 个交易日（分区覆盖，幂等）。
    水位照常推进到当天。远于 125 日的修订不传播 -> 月度全量重刷一次兜底。

[CAVEAT] 价格指数口径：index_daily / sw_daily 均为价格指数（非全收益）。
    红利/现金流类指数的 label 系统性低估真实持有收益（股息被丢弃），
    截面动量对高股息风格偏保守。v1 接受；H00922（中证红利全收益）可对拍偏差幅度。

验收锚点（作者本地跑完 backfill 后核对，证据落 research/）：
    1. PK 重复 = 0：SELECT ts_code, trade_date, COUNT(*) FROM label_index_forward_returns
       GROUP BY 1,2 HAVING COUNT(*)>1;
    2. 行数 = 各 code 自身交易日数之和（未发布期无行是正确行为）。
    3. 尾部结构性 NULL：最近 ~120 个交易日 ret_fwd_120 为 NULL（未 finalize，非缺口）。
    4. 抽样对拍：任取 (000300.SH, T)，ret_fwd_20 应等于
       close(T+21)/close(T+1)-1（对 index_daily 独立手算）。
    5. 覆盖率审计：每 code 的交易日数 vs trade_cal 自 max(该code起始日, 2014-02-21)，
       缺口数 = 源数据缺口（shift 口径下自动顺延，量化记录即可）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config.universe import (
    ALL_INDEX_CODES,
    CANONICAL_INDEX_CODES,
    SW_L1_LIST,
    canonical,
)
from core.dates import get_previous_n_trading_date, get_today_str
from data.label.base import LabelCalculator

logger = logging.getLogger(__name__)


class IndexForwardReturnsCalculator(LabelCalculator):
    """指数（宽基 + 风格 + 申万 L1）前向收益标签。

    每行 = (ts_code, trade_date) -> 未来 5/10/20/60/120 日收益（T+1 收盘入场口径）。
    ts_code：指数池用 canonical 代码（双版已归一去重）；行业用申万 L1 代码（801xxx.SI）。
    """

    table_name = "index_forward_returns"  # -> label_index_forward_returns
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    # 前向窗口（天）。5/10 供 IC 衰减曲线完整性与反转区诊断（计算层宽进），
    # 研究主轴仍为 20/60/120（design notes §8.6）。
    forward_windows: List[int] = [5, 10, 20, 60, 120]
    # 增量重刷窗 = max(forward_windows) + 1（finalize 距离）+ 4（缓冲）
    refresh_trailing_days: int = 125

    # ===== output_schema =====
    @property
    def output_schema(self) -> Dict[str, str]:  # type: ignore[override]
        schema = {"ts_code": "string", "trade_date": "date"}
        for n in self.forward_windows:
            # 未来 N 日收益（T+1 收盘买 / T+1+N 收盘卖），简单收益非对数
            schema[f"ret_fwd_{n}"] = "float"
        return schema

    # ===== 增量语义：固定回刷尾部（覆盖基类水位增量）=====
    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """增量模式（不传 start_date）= 固定重刷近 refresh_trailing_days 个交易日。

        原因：label 是前向的，行 T 要到 T+121 交易日才 finalize；按水位次日增量
        只会算出全 NULL 的新行。显式传 start_date 走回补（全量重算）。
        """
        if start_date is None:
            end = self._normalize_date(end_date) or get_today_str()
            start_date = get_previous_n_trading_date(end, self.refresh_trailing_days)
            self.logger.info(
                "[增量] 前向 label 需 T+1+%d 交易日 finalize，固定回刷 [%s, %s]",
                max(self.forward_windows),
                start_date,
                end,
            )
        return super().update(start_date=start_date, end_date=end_date, **params)

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """读 index_daily（指数池，含双版）+ sw_daily（申万 L1）的收盘价。

        只取 [start_date, end_date]：前向 label 只看未来价，无需回看 buffer。
        """
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        def code_in(codes: List[str]) -> str:
            return ",".join(f"'{c}'" for c in codes)

        # 指数池：canonical + 双版 alt 全取，process 前归一（早年部分日期
        # 只有某一版有行，双版全取保证不缺）
        idx_sql = f"""
            SELECT ts_code, trade_date, close
            FROM index_daily
            WHERE ts_code IN ({code_in(ALL_INDEX_CODES)})
              AND trade_date BETWEEN '{start_date}' AND '{end_date}'
        """
        idx = pd.read_sql(idx_sql, self.engine)
        self.logger.info(
            "index_daily: %d 行（%d 个代码）", len(idx), idx["ts_code"].nunique()
        )

        # 申万 L1 行业
        sw_sql = f"""
            SELECT ts_code, trade_date, close
            FROM sw_daily
            WHERE ts_code IN ({code_in(SW_L1_LIST)})
              AND trade_date BETWEEN '{start_date}' AND '{end_date}'
        """
        sw = pd.read_sql(sw_sql, self.engine)
        self.logger.info(
            "sw_daily: %d 行（%d 个代码）", len(sw), sw["ts_code"].nunique()
        )

        # 双版归一：先标记来源再 map（map 后 alt 已变 canonical，无法区分）。
        # 排序让 canonical 来源的行排前，drop_duplicates 保留 canonical 版，
        # alt 行只在 canonical 无该日数据时兜底（早年部分日期仅单版有行，两版收盘一致）。
        idx["src_canonical"] = idx["ts_code"].isin(CANONICAL_INDEX_CODES)
        idx["ts_code"] = idx["ts_code"].map(canonical)
        idx = (
            idx.sort_values(
                ["ts_code", "trade_date", "src_canonical"],
                ascending=[True, True, False],
            )
            .drop_duplicates(subset=["ts_code", "trade_date"], keep="first")
            .drop(columns="src_canonical")
        )

        return pd.concat([idx, sw], ignore_index=True)

    # ===== process_data =====
    def process_data(
        self, data: pd.DataFrame, **params: Any
    ) -> pd.DataFrame:
        """按指数自身日序列 shift 计算前向收益（口径见模块 docstring）。"""
        if data.empty:
            self.logger.warning("输入数据为空")
            return pd.DataFrame()

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # 只按 trade_date 排序即可满足 groupby(shift) 的组内保序要求
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        self.logger.info(
            "原始数据 %d 行，%d 个指数", len(df), df["ts_code"].nunique()
        )

        close = df.groupby("ts_code", sort=False)["close"]
        entry = close.shift(-1)  # T+1 收盘买入价

        out = df[["ts_code", "trade_date"]].copy()
        for n in self.forward_windows:
            # T+1+N 收盘卖出价 / T+1 买入价 - 1
            out[f"ret_fwd_{n}"] = close.shift(-(n + 1)) / entry - 1

        # 尾部未 finalize 的行保留 NaN（to_sql 写 NULL，§1.1 不做 NaN->None）
        out = out.replace([np.inf, -np.inf], np.nan)
        out["trade_date"] = out["trade_date"].dt.strftime("%Y%m%d")

        self.logger.info(
            "前向收益标签完成：%d 行，窗口 %s", len(out), self.forward_windows
        )
        return out
