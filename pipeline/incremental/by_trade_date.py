"""by_trade_date 增量策略：行情类，区间内逐交易日拉。

适用：daily / weekly / moneyflow / index_daily / fund_daily 等按交易日切片的接口。

子类实现 fetch_one_period(trade_date=YYYYMMDD, **params) -> DataFrame。
"""
import logging
from typing import List, Optional

import pandas as pd

from core.dates import get_trade_dates_between
from pipeline.incremental.base import BaseIncremental

logger = logging.getLogger(__name__)


class ByTradeDateCalculator(BaseIncremental):
    """行情类增量策略。

    - biz_date_col = "trade_date"
    - get_data 枚举 [start, end] 内每个交易日，逐日调 fetch_one_period(trade_date=...)
    - 单日失败不中断（记录 warning，继续下一天）
    """

    biz_date_col: str = "trade_date"

    def get_data(self, start_date: Optional[str], end_date: Optional[str], **params) -> pd.DataFrame:
        if not start_date or not end_date:
            logger.warning(
                "%s.get_data 需要 start_date 和 end_date，收到 start=%r end=%r",
                type(self).__name__, start_date, end_date,
            )
            return pd.DataFrame()

        trade_dates: List[str] = get_trade_dates_between(start_date, end_date)
        if not trade_dates:
            logger.info("%s.get_data 区间 [%s, %s] 无交易日", type(self).__name__, start_date, end_date)
            return pd.DataFrame()

        logger.info(
            "%s.get_data 拉取 [%s, %s] 共 %d 个交易日",
            type(self).__name__, start_date, end_date, len(trade_dates),
        )

        results: List[pd.DataFrame] = []
        for td in trade_dates:
            try:
                df = self.fetch_one_period(trade_date=td, **params)
            except Exception as e:
                logger.warning("%s.fetch_one_period(trade_date=%s) 失败: %s", type(self).__name__, td, e)
                continue
            if df is None or len(df) == 0:
                continue
            results.append(df)

        if not results:
            return pd.DataFrame()

        combined = pd.concat(results, ignore_index=True)
        logger.info("%s.get_data 完成，共 %d 行", type(self).__name__, len(combined))
        return combined
