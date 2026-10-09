"""申万 L1 行业成分 · 月末 as-of Panel（2026-10-09）。

表名：panel_sw_membership_monthly
主键：trade_date + ts_code（月末 × 股票；每股每月恰属一个 L1，区间不重叠）
biz_date_col：trade_date
write_mode：overwrite（partition_col=trade_date；批=完整月末分区，重刷精确替换——
            成分重算后行集可能缩小，upsert 会留脏行，故必须分区覆盖）
依赖：index_member_all（申万成分全量快照，7,920 行小表，含历史分段）

PIT 口径（as-of，防穿越）：
    成分(月末 m) = in_date <= m < out_date（out_date 为空 = 现任未退出）
    半开区间 [in_date, out_date)：in_date 当日已属新行业，out_date 当日已剔除。

数据事实（2026-10-09 审计，证据见 design notes §4.2）：
    - 5,914 现任段 + 2,006 历史段（out_date 非空），out_date 分布 1999-2026，
      2021 年 587 条 = SW2021 版切换记录——历史改判已被 tushare 回填。
    - 每股最多 6 段归属；全部段映射到 SW2021 的 31 个 L1 码。
    - [CAVEAT] 退市股不在快照（幸存者偏差）：早年行业成分系统性略少。
      宽度（比例）/ PE 聚合（加总）口径下影响二阶，不修补。

月频固化（同 DEV_GUIDE §4.5）：只写"完整月"的月末——当月真实月末交易日未到
则该月零写入，月中调用安全。月末集合取自 trade_cal 全月真值（非"<=今天的最后一天"）。

验收锚点（backfill 后核对）：
    1. PK 零重复。
    2. 每月成分数与当月全市场股票数同量级（2010 ~2,000s -> 2026 ~5,400）。
    3. 每月覆盖 31 个 L1 全集。
    4. 抽样改判股：out_date 落在月中的股票，前一月末属旧行业、后一月末属新行业。
    5. 月中运行零写入（固化）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from core.dates import get_monthly_last_tradedate, get_today_str
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class SwMembershipMonthlyCalculator(PanelCalculator):
    """申万 L1 行业成分月末 as-of 快照（每股每月一行）。"""

    table_name = "sw_membership_monthly"  # -> panel_sw_membership_monthly
    primary_keys = ["trade_date", "ts_code"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"

    # 研究起点年（与 panel_index_membership_monthly 对齐；in_date 数据本身可回溯到 1984）
    start_year: int = 2010

    output_schema = {
        "ts_code": "string",
        "trade_date": "date",
        "l1_code": "string",
        "l1_name": "string",
    }

    # ===== update：月频固化（只写完整月末）=====
    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """增量（不传 start_date）= 水位次月起的新完整月；传 = 回补。"""
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date) or get_today_str()
        if not start_date:
            watermark_next = self._next_after_biz_date()
            # 水位次月 1 日起（水位所在月已写过）；无水位则默认起点
            start_date = (
                watermark_next[:6] + "01" if watermark_next else f"{self.start_year}0101"
            )

        month_ends = self._complete_month_ends(start_date, end_date)
        self.logger.info(
            "[月频] [%s, %s] 完整月末 %d 个%s",
            start_date,
            end_date,
            len(month_ends),
            "" if month_ends else "（本月月末未到，零写入）",
        )
        if not month_ends:
            return pd.DataFrame()

        raw = self.get_data()  # 快照小表全读（无日期维度）
        result = self.process_data(raw, month_ends=month_ends)
        if result.empty:
            self.logger.warning("process_data 返回空，跳过")
            return pd.DataFrame()

        self.save_to_database(result)
        max_biz = self._max_biz_date(result)
        if max_biz:
            self._set_biz_date(max_biz, len(result))
        return result

    def _complete_month_ends(self, start_date: str, end_date: str) -> List[str]:
        """[start, end] 内"真实月末交易日"集合。

        用 trade_cal 的全月真值（含未来日历）：当月月末未到时该月自动缺席，
        保证月中运行零写入。
        """
        sy, ey = int(start_date[:4]), int(end_date[:4])
        all_month_ends = get_monthly_last_tradedate(self.engine, sy, ey)
        return [m for m in all_month_ends if start_date <= m <= end_date]

    # ===== get_data =====
    def get_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """读 index_member_all 全量分段（约 8k 行，无日期过滤必要）。"""
        return pd.read_sql(
            "SELECT ts_code, l1_code, l1_name, in_date, out_date FROM index_member_all",
            self.engine,
        )

    # ===== process_data =====
    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """逐月末做 as-of 区间过滤：in_date <= m < out_date。"""
        month_ends: List[str] = params.get("month_ends") or []
        if data.empty or not month_ends:
            return pd.DataFrame()

        seg = data[["ts_code", "l1_code", "l1_name", "in_date", "out_date"]].copy()
        seg["in_date"] = pd.to_datetime(seg["in_date"])
        seg["out_date"] = pd.to_datetime(seg["out_date"])

        frames = []
        for m in month_ends:
            md = pd.Timestamp(m)
            mask = (seg["in_date"] <= md) & (
                seg["out_date"].isna() | (seg["out_date"] > md)
            )
            cur = seg.loc[mask, ["ts_code", "l1_code", "l1_name", "in_date"]].copy()
            if cur.empty:
                continue
            cur["trade_date"] = m
            frames.append(cur)
        if not frames:
            return pd.DataFrame()

        out = pd.concat(frames, ignore_index=True)
        # 防御：源区间若异常重叠导致同股同月多段，保留 in_date 最新一段
        out = (
            out.sort_values(["trade_date", "ts_code", "in_date"])
            .drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
            .reset_index(drop=True)
        )
        result = out[["ts_code", "trade_date", "l1_code", "l1_name"]]

        # 审计日志：月均成分数 + L1 覆盖范围
        per_month_l1 = result.groupby("trade_date")["l1_code"].nunique()
        self.logger.info(
            "sw membership: %d 个月末，月均 %d 只股票；每月 L1 覆盖 min %d / max %d",
            result["trade_date"].nunique(),
            int(len(result) / result["trade_date"].nunique()),
            int(per_month_l1.min()),
            int(per_month_l1.max()),
        )
        return result
