"""by_ex_date 增量策略：分红类，按除权除息日(ex_date)取数 + overwrite 覆盖。

适用：dividend（只关心"实施"阶段的真实分红，ex_date 非空才有除权）。

为什么用 ex_date 而非 ann_date/period：
- dividend 接口无 period 参数，无法按报告期完整取数
- 业务上只关心真实分红 → ex_date 非空的"实施"记录才是有效分红
- 按 ex_date 取数天然过滤掉"预案/股东大会通过"（ex_date=null，不被命中）
- ex_date 就是除权除息日，必为交易日 → 可复用交易日历枚举

幂等：配合 write_mode=overwrite + partition_col=ex_date，某日重拉=删该日除权记录+重写。

子类（TushareByExDateCalculator）实现 fetch_one_period(ex_date=YYYYMMDD, **params)。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Any, List, Optional

import pandas as pd

from core.dates import get_today_str, get_trade_dates_between
from pipeline.incremental.base import BaseIncremental

logger = logging.getLogger(__name__)

# 分红增量保守重刷窗口（天）。分红除权散布全年、生命周期长（预案→实施跨数月），
# 固定回刷最近 1 年覆盖期间任何修订/补录/推迟。overwrite 幂等，重刷不脏。
DEFAULT_DIV_LOOKBACK_DAYS = 365


class ByExDateCalculator(BaseIncremental):
    """分红类增量策略（按除权除息日）。

    - biz_date_col = "ex_date"
    - get_data 枚举 [start, end] 内每个交易日，逐日调 fetch_one_period(ex_date=...)
    - 单日失败不中断（记录 warning，继续下一天）
    - 覆盖 update：增量起点取「水位」与「today-lookback」中更早的（见 update 注释）
    """

    biz_date_col: str = "ex_date"
    lookback_days: int = DEFAULT_DIV_LOOKBACK_DAYS

    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """分红更新。两种入口：

        - 回补（外部传了 start_date）：原样透传 → 精确取该 ex_date 区间。
        - 增量（外部没传 start_date）：起点取「水位」与「today-lookback」中更早的：
            · 经常开机(水位新)：today-365 更早 → 回刷最近 1 年，覆盖分红修订/补录
            · 久未开机(水位旧)：水位 更早 → 从水位补到今天，不漏中间断档
          二者取早 = 既覆盖修订、又不漏数。overwrite 幂等，重叠期重刷无副作用。
        """
        if not start_date:
            end_d = self._normalize_date(end_date) or get_today_str()
            floor = (
                datetime.strptime(end_d, "%Y%m%d")
                - timedelta(days=self.lookback_days)
            ).strftime("%Y%m%d")
            watermark = self._get_biz_date()  # 已入库最大 ex_date，或 None
            start_date = min(watermark, floor) if watermark else floor
            end_date = end_d
            self.logger.info(
                f"{self.table_name} 增量(从 {start_date} 起，水位={watermark} "
                f"兜底最近 {self.lookback_days} 天)"
            )
        # 走基类 update：get_data(逐 ex_date) → process → overwrite 落库 → 更新水位
        return super().update(start_date=start_date, end_date=end_date, **params)

    def get_data(
        self, start_date: Optional[str], end_date: Optional[str], **params
    ) -> pd.DataFrame:
        if not start_date or not end_date:
            logger.warning(
                "%s.get_data 需要 start_date 和 end_date，收到 start=%r end=%r",
                type(self).__name__, start_date, end_date,
            )
            return pd.DataFrame()

        trade_dates: List[str] = get_trade_dates_between(start_date, end_date)
        if not trade_dates:
            logger.info(
                "%s.get_data 区间 [%s, %s] 无交易日",
                type(self).__name__, start_date, end_date,
            )
            return pd.DataFrame()

        logger.info(
            "%s.get_data 拉取 ex_date [%s, %s] 共 %d 个交易日",
            type(self).__name__, start_date, end_date, len(trade_dates),
        )

        results: List[pd.DataFrame] = []
        for i, ex_d in enumerate(trade_dates):
            try:
                df = self.fetch_one_period(ex_date=ex_d, **params)
            except Exception as e:
                logger.warning(
                    "%s.fetch_one_period(ex_date=%s) 失败: %s",
                    type(self).__name__, ex_d, e,
                )
                continue
            if df is None or len(df) == 0:
                continue
            results.append(df)
            if (i + 1) % 10 == 0:
                time.sleep(0.5)  # 防 tushare 限频

        if not results:
            return pd.DataFrame()

        combined = pd.concat(results, ignore_index=True)
        logger.info("%s.get_data 完成，共 %d 行", type(self).__name__, len(combined))
        return combined
