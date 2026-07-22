"""Panel 计算层基类（data/panel/）。

设计（CLAUDE.md 2.2 / 硬约束 4）：
- 继承 core.calculator.BaseCalculator（统一 update 入口 + schema-as-code + 水位）
- 表名加 panel_ 前缀（与 etl 接入层表区分）
- output_schema：加工层手写 schema dict（声明输出列类型，避免自动推断不准）
- biz_date_col：panel 数据按 trade_date / ann_date / snapshot_date 增量
- write_mode：默认 overwrite（按 partition_col 先删再批量写，幂等 + 去重护栏）

子类声明：
    table_name: str          # 不含 panel_ 前缀（基类自动加）
    primary_keys: list[str]  # 主键列
    biz_date_col: str        # 业务日期列（trade_date / ann_date / snapshot_date）
    output_schema: dict      # 输出列类型 {col: "float"/"int"/"string"/"date"/"bool"}
    write_mode: str = "overwrite"
    partition_col: str       # overwrite 必填（trade_date / snapshot_date / end_date）

子类实现：
    get_data(start_date, end_date, **params) -> DataFrame
    process_data(data, **params) -> DataFrame
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from config.database import engine
from core.calculator import BaseCalculator

logger = logging.getLogger(__name__)


class PanelCalculator(BaseCalculator):
    """Panel 计算层基类。

    - table_name 不含 panel_ 前缀（本类自动加）
    - output_schema 由子类声明（加工层必须手写，不用自动推断）
    - write_mode 默认 overwrite（按 partition_col 先删再批量写，幂等）
    """

    # 子类覆盖
    primary_keys: List[str] = []
    biz_date_col: str = "trade_date"
    write_mode: str = "overwrite"
    output_schema: Optional[Dict[str, str]] = None

    # 前缀（panel 表统一加，与 etl 接入层表区分）
    TABLE_PREFIX: str = "panel_"

    def __init__(self, engine=None):
        """初始化。engine 默认用全局 engine。"""
        super().__init__(engine=engine)
        # 加前缀（如果子类声明的 table_name 已含前缀则不重复加）
        if not self.table_name.startswith(self.TABLE_PREFIX):
            self.table_name = f"{self.TABLE_PREFIX}{self.table_name}"
        if not self.output_schema:
            self.logger.warning(
                "%s 未声明 output_schema，将用自动推断（可能不准）",
                self.__class__.__name__,
            )

    def get_data(self, start_date: str, end_date: str, **params: Any) -> pd.DataFrame:
        """取数（子类必须实现）。

        start_date/end_date 是 biz_date 区间。子类用 pd.read_sql + WHERE trade_date BETWEEN 查辅助表。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 必须实现 get_data")

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        """计算（子类必须实现）。

        接收 get_data 的输出，做 join/计算/转换。update 会透传 start_date/end_date。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 必须实现 process_data")
