"""by_period 增量策略：财务类，按报告期(period=end_date)取数 + overwrite 覆盖。

适用：income / balancesheet / cashflow 等 vip 财报接口（按 period 拉全市场）。

为什么不用 by_ann_date：
- tushare 财务 vip 按 period 取一次 = 该报告期全市场全部版本（最完整）
- 修正版（同报告期不同 f_ann_date）只有"重拉整个 period"才能覆盖
- 配合 write_mode=overwrite（按 end_date 删后写），period 维度天然幂等

增量 vs 回补：
- update(start_date=None)        → 增量：重拉最近 N 个报告期（覆盖最新修正）
- update(start_date=, end_date=) → 回补：区间内所有季度末报告期

子类（TushareByPeriodCalculator）实现 fetch_one_period(period=YYYYMMDD, **params)。
"""
import logging
from datetime import datetime
from typing import Any, List, Optional

import pandas as pd

from core.dates import get_today_str
from pipeline.incremental.base import BaseIncremental

logger = logging.getLogger(__name__)

# 增量默认重拉最近 N 个报告期（多覆盖防漏修正版）
DEFAULT_RECENT_N_PERIODS = 4

_QUARTER_ENDS = ("0331", "0630", "0930", "1231")


class ByPeriodCalculator(BaseIncremental):
    """财务类增量策略（按报告期）。

    - biz_date_col = "end_date"（报告期即业务日期）
    - 覆盖 update：自己拆分 period 列表，逐期 fetch_one_period(period=...)
    - 落库走 BaseCalculator.save_to_database（write_mode=overwrite, partition_col=end_date）
    """

    biz_date_col: str = "end_date"
    recent_n_periods: int = DEFAULT_RECENT_N_PERIODS

    # ===== 覆盖 update：period 语义 =====
    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        end_d = self._normalize_date(end_date) or get_today_str()

        if start_date:
            # 回补：区间内所有季度末报告期
            periods = self._quarter_ends_between(self._normalize_date(start_date), end_d)
            mode = "回补"
        else:
            # 增量：起点取「水位」与「today 往前 N 期」中更早的那个。
            #   - 经常开机(水位新)：today-N期 更早 → 刷最近 N 期，覆盖财报修订
            #   - 久未开机(水位旧)：水位 更早 → 从水位补到今天，不漏中间断档
            # 二者取早 = 既覆盖修订、又不漏数。overwrite 幂等，重叠期重刷无副作用。
            recent = self._recent_quarter_ends(end_d, self.recent_n_periods)
            floor_period = recent[-1] if recent else end_d  # today 往前第 N 期（最早）
            watermark = self._get_biz_date()                # 已入库最大 end_date，或 None
            start_period = (
                min(watermark, floor_period) if watermark else floor_period
            )
            periods = self._quarter_ends_between(start_period, end_d)
            mode = f"增量(从 {start_period} 起，水位={watermark} 兜底最近{self.recent_n_periods}期)"

        if not periods:
            self.logger.warning(f"{self.table_name} update：未解析出任何报告期，跳过")
            return pd.DataFrame()

        self.logger.info(
            f"{self.table_name} by_period update（{mode}）: periods={periods}"
        )

        frames: List[pd.DataFrame] = []
        for period in periods:
            try:
                df = self.fetch_one_period(period=period, **params)
            except Exception as e:
                self.logger.error(
                    f"{self.table_name} fetch_one_period(period={period}) 失败: {e}"
                )
                continue
            if df is not None and len(df) > 0:
                frames.append(df)
                self.logger.info(f"  period={period}: {len(df)} 行")

        if not frames:
            self.logger.warning(f"{self.table_name} 所有 period 均无数据，跳过")
            return pd.DataFrame()

        raw = pd.concat(frames, ignore_index=True)
        result = self.process_data(raw, start_date=start_date, end_date=end_d, **params)
        if result is None or result.empty:
            self.logger.warning(f"{self.table_name} process_data 返回空，跳过")
            return pd.DataFrame()

        # 落库（overwrite by end_date，幂等）
        self.save_to_database(result)

        # 水位 = 本批最大 end_date
        if self.biz_date_col and self.biz_date_col in result.columns:
            max_biz = self._max_biz_date(result)
            if max_biz:
                self._set_biz_date(max_biz, len(result))
        return result

    # get_data 在本策略不单独使用（update 已自包含），保留兜底实现
    def get_data(
        self, start_date: Optional[str], end_date: Optional[str], **params
    ) -> pd.DataFrame:
        periods = (
            self._quarter_ends_between(start_date, end_date)
            if start_date
            else self._recent_quarter_ends(end_date or get_today_str(), self.recent_n_periods)
        )
        frames = [
            df
            for p in periods
            if (df := self.fetch_one_period(period=p, **params)) is not None and len(df)
        ]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # ===== 报告期工具 =====
    @staticmethod
    def _recent_quarter_ends(today: str, n: int) -> List[str]:
        """返回 <= today 的最近 n 个季度末（倒序），如
        today=20240620 → ['20240331','20231231','20230930','20230630']。
        """
        today = today.replace("-", "")
        y = int(today[:4])
        ends: List[str] = []
        # 枚举近几年所有季度末（倒序），筛 <= today
        for yy in range(y, y - (n // 4 + 2), -1):
            for mmdd in reversed(_QUARTER_ENDS):
                qend = f"{yy}{mmdd}"
                if qend <= today:
                    ends.append(qend)
        return ends[:n]

    @staticmethod
    def _quarter_ends_between(start: str, end: str) -> List[str]:
        """返回 [start, end] 区间内所有季度末报告期（升序）。"""
        start = start.replace("-", "")
        end = end.replace("-", "")
        sy, ey = int(start[:4]), int(end[:4])
        ends: List[str] = []
        for yy in range(sy, ey + 1):
            for mmdd in _QUARTER_ENDS:
                qend = f"{yy}{mmdd}"
                if start <= qend <= end:
                    ends.append(qend)
        return ends
