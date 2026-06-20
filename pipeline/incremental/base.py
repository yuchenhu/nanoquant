"""增量策略基类。

子类实现 `fetch_one_period(**params) -> DataFrame` 拉单批数据，
基类（继承自 core.calculator.BaseCalculator）负责 update 流程 + 水位。
策略子类（by_trade_date / by_ann_date / full_refresh）负责 get_data 的区间拆分。
"""
from typing import Optional

import pandas as pd

from core.calculator import BaseCalculator


class BaseIncremental(BaseCalculator):
    """增量策略抽象基类。

    子类必须实现：
        fetch_one_period(**params) -> DataFrame
            拉单批数据（单日 / 单公告区间 / 全量），返回原始 DataFrame。

    子类必须实现 get_data（按策略拆分区间调 fetch_one_period），
    或直接继承 ByTradeDateCalculator / ByAnnDateCalculator / FullRefreshCalculator。
    """

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        """拉单批数据。子类实现。"""
        raise NotImplementedError(
            f"{type(self).__name__} 必须实现 fetch_one_period(**params) -> DataFrame"
        )

    def get_data(self, start_date: Optional[str], end_date: Optional[str], **params) -> pd.DataFrame:
        """按策略拆分 [start_date, end_date] 区间，逐批调 fetch_one_period。子类实现。"""
        raise NotImplementedError(
            f"{type(self).__name__} 必须实现 get_data 或继承具体策略基类"
        )
