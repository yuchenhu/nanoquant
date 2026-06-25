"""data/panel/ 包：Panel 计算层（结构化数据加工）。

7 个 Panel Calculator（从 data/sql/ 迁移）：
- StockDailyPanelCalculator: 个股×日 行情宽表（panel_stock_daily）
- StockPercentilesCalculator: 个股×日 历史百分位（panel_stock_percentiles）
- MarketSentimentDailyCalculator: 市场×日 情绪（panel_market_sentiment_daily）
- MarketSentimentMonthlyCalculator: 市场×月 情绪（panel_market_sentiment_monthly）
- MvMonthlyCalculator: 个股×月 市值快照（panel_mv_monthly）
- FinancialStatementsSnapshotCalculator: 个股×报告期 财报三表快照（panel_financial_statements_snapshot）
- FinancialIndicatorsSnapshotCalculator: 个股×报告期 财务指标快照（panel_financial_indicators_snapshot）

统一继承 data.panel.base.PanelCalculator（→ core.calculator.BaseCalculator）。
表名自动加 panel_ 前缀。
"""
from data.panel.base import PanelCalculator
from data.panel.data_quality import DataQualityCalculator
from data.panel.financial_indicators_snapshot import (
    FinancialIndicatorsSnapshotCalculator,
)
from data.panel.financial_statements_snapshot import (
    FinancialStatementsSnapshotCalculator,
)
from data.panel.index_membership_monthly import IndexMembershipMonthlyCalculator
from data.panel.market_sentiment_daily import MarketSentimentDailyCalculator
from data.panel.market_sentiment_monthly import (
    MarketSentimentMonthlyCalculator,
)
from data.panel.mv_monthly import MvMonthlyCalculator
from data.panel.stock_daily_panel import StockDailyPanelCalculator
from data.panel.stock_percentiles import StockPercentilesCalculator

__all__ = [
    "PanelCalculator",
    "StockDailyPanelCalculator",
    "StockPercentilesCalculator",
    "MarketSentimentDailyCalculator",
    "MarketSentimentMonthlyCalculator",
    "MvMonthlyCalculator",
    "FinancialStatementsSnapshotCalculator",
    "FinancialIndicatorsSnapshotCalculator",
    "IndexMembershipMonthlyCalculator",
    "DataQualityCalculator",
]

# Panel Calculator 注册表（供 pipeline 调度）
PANEL_CALCULATORS = {
    "stock_daily": StockDailyPanelCalculator,
    "stock_percentiles": StockPercentilesCalculator,
    "market_sentiment_daily": MarketSentimentDailyCalculator,
    "market_sentiment_monthly": MarketSentimentMonthlyCalculator,
    "mv_monthly": MvMonthlyCalculator,
    "financial_statements_snapshot": FinancialStatementsSnapshotCalculator,
    "financial_indicators_snapshot": FinancialIndicatorsSnapshotCalculator,
    "index_membership_monthly": IndexMembershipMonthlyCalculator,
    "data_quality": DataQualityCalculator,
}
