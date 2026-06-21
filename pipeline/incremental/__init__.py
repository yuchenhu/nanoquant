"""增量策略（生产用四类 + 兼容保留 by_ann_date）。

- ByTradeDateCalculator: 行情类，区间内逐交易日拉（biz_date_col=trade_date，overwrite）
- ByPeriodCalculator:     财务类，按报告期(period=end_date)取全市场（overwrite，增量起点 min(水位,today-4期)）
- ByExDateCalculator:     分红类，按除权日(ex_date)逐交易日拉（overwrite，增量起点 min(水位,today-365天)）
- FullRefreshCalculator:  基础信息类，每次全量 truncate（无 biz_date_col）
- ByAnnDateCalculator:    旧财务区间策略，保留兼容，已不用于生产

接入层 Calculator 继承对应策略基类，实现 `fetch_one_period(**params)` 拉单批数据，
基类负责按 biz_date 区间循环 + 水位更新（水位逻辑在 core.calculator.BaseCalculator）。
"""
from pipeline.incremental.base import BaseIncremental  # noqa: F401
from pipeline.incremental.by_trade_date import ByTradeDateCalculator  # noqa: F401
from pipeline.incremental.by_period import ByPeriodCalculator  # noqa: F401
from pipeline.incremental.by_ex_date import ByExDateCalculator  # noqa: F401
from pipeline.incremental.by_ann_date import ByAnnDateCalculator  # noqa: F401
from pipeline.incremental.full_refresh import FullRefreshCalculator  # noqa: F401
