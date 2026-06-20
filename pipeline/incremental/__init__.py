"""三类增量策略。

- ByTradeDateCalculator: 行情类，区间内逐交易日拉（biz_date_col=trade_date）
- ByAnnDateCalculator:   财务类，按 ann_date 区间拉 + 回看覆盖修订（biz_date_col=ann_date）
- FullRefreshCalculator: 基础信息类，每次全量 truncate（无 biz_date_col）

接入层 Calculator 继承对应策略基类，实现 `fetch_one_period(**params)` 拉单批数据，
基类负责按 biz_date 区间循环 + 水位更新（水位逻辑在 core.calculator.BaseCalculator）。
"""
from pipeline.incremental.base import BaseIncremental  # noqa: F401
from pipeline.incremental.by_trade_date import ByTradeDateCalculator  # noqa: F401
from pipeline.incremental.by_ann_date import ByAnnDateCalculator  # noqa: F401
from pipeline.incremental.full_refresh import FullRefreshCalculator  # noqa: F401
