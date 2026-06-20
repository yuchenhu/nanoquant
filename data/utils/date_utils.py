"""兼容 shim：重导出顶层 core.dates。

新代码请直接 `from core.dates import ...`。
本文件在 Step 9 所有调用方迁移完后删除。
"""
from core.dates import (  # noqa: F401
    _TRADE_CAL_DF,
    _get_trade_cal,
    reload_trade_cal,
    get_today_str,
    get_recent_quarter_dates,
    get_month_start_end,
    is_trading_day,
    find_nearest_trading_day,
    get_previous_n_trading_date,
    get_next_n_trading_date,
    get_recent_weekday,
    get_recent_month,
    get_monthly_last_tradedate,
)
