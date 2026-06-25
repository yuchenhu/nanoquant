"""by_ann_date 增量策略：财务类，按 ann_date 区间拉 + 回看覆盖修订。

适用：income / balancesheet / cashflow / forecast / express / fina_indicator 等财报接口。

财务数据特性：
- 同一条记录可能因财报修订被多次发布（ann_date 变化）
- 用 ann_date 做水位（最近一次拉到的公告日）
- 每次拉取时回看 N 天（默认 30 天）覆盖修订

子类实现 fetch_one_period(start_ann_date=..., end_ann_date=..., **params) -> DataFrame。
"""
import logging
from typing import Optional

import pandas as pd

from pipeline.incremental.base import BaseIncremental

logger = logging.getLogger(__name__)

# 财务数据回看窗口：覆盖最近 N 天的修订
DEFAULT_LOOKBACK_DAYS = 30


class ByAnnDateCalculator(BaseIncremental):
    """财务类增量策略。

    - biz_date_col = "ann_date"
    - get_data 以 [start_date, end_date] 为 ann_date 区间，单批拉取
    - 水位回看：实际拉取区间 = [max(start_date, last_biz_date - lookback_days), end_date]
    - 子类实现 fetch_one_period(start_ann_date=..., end_ann_date=..., **params)
    """

    biz_date_col: str = "ann_date"
    lookback_days: int = DEFAULT_LOOKBACK_DAYS

    def get_data(self, start_date: Optional[str], end_date: Optional[str], **params) -> pd.DataFrame:
        if not start_date or not end_date:
            logger.warning(
                "%s.get_data 需要 start_date 和 end_date，收到 start=%r end=%r",
                type(self).__name__, start_date, end_date,
            )
            return pd.DataFrame()

        # 水位回看：从上次水位往前推 lookback_days，覆盖修订
        lookback_start = self._shift_date(start_date, -self.lookback_days)
        fetch_start = min(start_date, lookback_start)

        logger.info(
            "%s.get_data 拉取 ann_date 区间 [%s, %s]（水位 %s 回看 %d 天）",
            type(self).__name__, fetch_start, end_date, start_date, self.lookback_days,
        )

        try:
            df = self.fetch_one_period(
                start_ann_date=fetch_start,
                end_ann_date=end_date,
                **params,
            )
        except Exception as e:
            logger.error("%s.fetch_one_period 失败: %s", type(self).__name__, e)
            return pd.DataFrame()

        if df is None or len(df) == 0:
            logger.info("%s.get_data 无数据", type(self).__name__)
            return pd.DataFrame()

        logger.info("%s.get_data 完成，共 %d 行", type(self).__name__, len(df))
        return df

    @staticmethod
    def _shift_date(date_str: str, days: int) -> str:
        """日期字符串加减天数（YYYYMMDD 格式）。"""
        from datetime import datetime, timedelta
        d = datetime.strptime(date_str, "%Y%m%d") + timedelta(days=days)
        return d.strftime("%Y%m%d")
