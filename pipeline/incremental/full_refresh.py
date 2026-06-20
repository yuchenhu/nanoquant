"""full_refresh 增量策略：基础信息类，每次全量 truncate。

适用：stock_basic / fund_basic / index_basic / trade_cal / hs_const / namechange /
     concept / concept_detail 等低频全量刷新的接口。

子类实现 fetch_one_period(**params) -> DataFrame（拉全量，无日期参数）。
"""
import logging
from typing import Optional

import pandas as pd

from pipeline.incremental.base import BaseIncremental

logger = logging.getLogger(__name__)


class FullRefreshCalculator(BaseIncremental):
    """基础信息类全量刷新策略。

    - 无 biz_date_col（水位不按日期）
    - get_data 单批拉全量，忽略 start_date / end_date
    - save_to_database 前先 truncate 目标表（write_mode = "truncate"）
    """

    biz_date_col: str = ""  # 全量刷新无业务日期
    write_mode: str = "truncate"

    def get_data(self, start_date: Optional[str], end_date: Optional[str], **params) -> pd.DataFrame:
        logger.info("%s.get_data 全量拉取（忽略日期区间）", type(self).__name__)
        try:
            df = self.fetch_one_period(**params)
        except Exception as e:
            logger.error("%s.fetch_one_period 失败: %s", type(self).__name__, e)
            return pd.DataFrame()

        if df is None or len(df) == 0:
            logger.info("%s.get_data 无数据", type(self).__name__)
            return pd.DataFrame()

        logger.info("%s.get_data 完成，共 %d 行", type(self).__name__, len(df))
        return df
