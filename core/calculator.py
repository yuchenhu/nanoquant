"""BaseCalculator：所有接入层/加工层 Calculator 的基类。

设计（见 CLAUDE.md 2.5）：
- 子类声明：table_name / primary_keys / write_mode / biz_date_col / output_schema
- 子类实现：get_data(start_date, end_date, **params) + process_data(data, **params)
- 统一入口 update(start_date, end_date, **params)：
  - start_date=None → 从 etl_biz_date 水位次日续跑（增量）
  - start_date 指定 → 从该 biz_date 回补（手动补数）
  - end_date=None → 到今天
  - **params 透传给 get_data / process_data
- biz_date_col：业务日期列名（trade_date / ann_date / snapshot_date），决定水位列
- output_schema：加工层手写 schema dict（接入层可不写，自动推断）

废弃旧 BaseCalculator 的 batch_process / incremental_update（功能重复，统一 update）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import inspect, text

from config.database import engine, overwrite_by_partition, save_to_database
from core.dates import get_today_str
from core.schema import (
    convert_date_columns,
    ensure_table,
    evolve_schema,
    infer_schema_from_df,
)

logger = logging.getLogger(__name__)


class BaseCalculator:
    """所有 Calculator 的基类。

    子类必须覆盖：
        table_name: str          # 目标表名
        biz_date_col: str        # 业务日期列（trade_date / ann_date / snapshot_date）
        primary_keys: list[str]  # 主键列
        write_mode: str          # overwrite / truncate / append

    子类必须实现：
        get_data(start_date, end_date, **params) -> DataFrame
        process_data(data, **params) -> DataFrame

    子类可选：
        output_schema: dict      # 加工层手写 schema（接入层可不写，自动推断）
        type_overrides: dict     # 接入层类型微调（如 {'desc': 'TEXT'}）
    """

    # ===== 子类覆盖的类属性 =====
    table_name: str = ""
    biz_date_col: str = "trade_date"
    primary_keys: List[str] = []
    write_mode: str = "overwrite"
    partition_col: Optional[str] = None  # write_mode=overwrite 时的分区键（删除粒度）
    output_schema: Optional[Dict[str, str]] = None
    type_overrides: Optional[Dict[str, str]] = None

    def __init__(self, engine=None):
        """初始化。engine 默认用全局 engine。"""
        self.engine = engine or globals()["engine"]
        self.logger = logging.getLogger(self.__class__.__name__)
        if not self.table_name:
            raise ValueError(f"{self.__class__.__name__} 必须声明 table_name")

    # ===== 子类实现 =====

    def get_data(self, start_date: str, end_date: str, **params) -> pd.DataFrame:
        """取数（子类必须实现）。

        start_date/end_date 是 biz_date 区间。逐日/逐月/逐快照怎么取，是子类内部实现。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 必须实现 get_data")

    def process_data(self, data: pd.DataFrame, **params) -> pd.DataFrame:
        """计算（子类必须实现）。

        接入层：只做日期转换等轻处理，忽略 start_date/end_date。
        加工层：可按 biz_date 区间查辅助表（update 会透传 start_date/end_date）。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 必须实现 process_data")

    # ===== 统一入口 =====

    def update(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **params: Any,
    ) -> pd.DataFrame:
        """把数据更新到覆盖 [start_date, end_date] 的 biz_date 区间。

        - start_date=None → 从 etl_biz_date 水位次日续跑（增量，自动定时任务用）
        - start_date 指定 → 从该 biz_date 回补（手动补数用）
        - end_date=None → 到今天
        - **params：非时间参数，透传给 get_data / process_data
        """
        start_date = self._normalize_date(start_date) or self._next_after_biz_date()
        end_date = self._normalize_date(end_date) or get_today_str()

        # 无水位时兜底：从 3 天前开始（覆盖周末/节假日，保证至少拉一点数据）
        if not start_date:
            from datetime import datetime, timedelta
            start_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            self.logger.info(
                f"{self.table_name} 无水位的首次运行，start 兜底为 {start_date}"
            )

        self.logger.info(
            f"{self.table_name} update: biz_date [{start_date}, {end_date}], "
            f"params={list(params.keys())}"
        )

        raw = self.get_data(start_date, end_date, **params)
        if raw is None or raw.empty:
            self.logger.warning(f"{self.table_name} get_data 返回空，跳过")
            return pd.DataFrame()

        result = self.process_data(
            raw, start_date=start_date, end_date=end_date, **params
        )
        if result is None or result.empty:
            self.logger.warning(f"{self.table_name} process_data 返回空，跳过")
            return pd.DataFrame()

        self.save_to_database(result)

        # 水位 = 本批 biz_date 最大值
        if self.biz_date_col and self.biz_date_col in result.columns:
            max_biz = self._max_biz_date(result)
            if max_biz:
                self._set_biz_date(max_biz, len(result))
        return result

    # ===== 落库（集成 schema-as-code） =====

    def save_to_database(self, data: pd.DataFrame) -> None:
        """落库：自动建表/演化 schema + 日期转换 + 主键去重 + write_mode 写入。"""
        schema = self._resolve_schema(data)
        ensure_table(self.table_name, schema, self.primary_keys)
        data = convert_date_columns(data, schema)

        # 落库前按主键去重（tushare 偶发重复，覆盖所有 write_mode）
        data = self._dedup_by_pk(data)

        if self.write_mode == "overwrite":
            if not self.partition_col:
                raise ValueError(
                    f"{self.table_name} write_mode=overwrite 必须声明 partition_col"
                )
            overwrite_by_partition(
                data,
                self.table_name,
                self.partition_col,
                engine=self.engine,
                primary_keys=self.primary_keys,
            )
        else:
            ok = save_to_database(
                data, self.table_name, self.write_mode, engine=self.engine
            )
            if not ok:
                raise RuntimeError(
                    f"{self.table_name} 落库失败（write_mode={self.write_mode}），"
                    f"见上方 ERROR 日志"
                )
        self.logger.info(
            f"{self.table_name} 落库 {len(data)} 行，write_mode={self.write_mode}"
        )

    def _dedup_by_pk(self, data: pd.DataFrame) -> pd.DataFrame:
        """按主键去重。有 update_flag 则留最大版本，否则留最后一条。

        重复时打 WARNING 并显式列出被删主键值（不静默吞）。
        overwrite_by_partition 内部也有去重，此处是兜底（覆盖 truncate/append 路径）。
        """
        pk = [c for c in self.primary_keys if c in data.columns]
        if not pk:
            return data
        dup_mask = data.duplicated(subset=pk, keep=False)
        n_dup = int(dup_mask.sum())
        if n_dup == 0:
            return data

        dup_keys = (
            data.loc[dup_mask, pk]
            .drop_duplicates()
            .head(20)
            .to_dict("records")
        )
        self.logger.warning(
            "!!! %s 发现 %d 行重复主键（主键=%s），数据源可能异常 !!!",
            self.table_name, n_dup, pk,
        )
        for k in dup_keys:
            self.logger.warning("    重复主键: %s", k)
        if len(dup_keys) == 20:
            self.logger.warning("    （仅显示前 20 组重复主键，可能还有更多）")

        before = len(data)
        if "update_flag" in data.columns:
            data = data.copy()
            data["_uf"] = pd.to_numeric(
                data["update_flag"], errors="coerce"
            ).fillna(0)
            data = (
                data.sort_values(pk + ["_uf"])
                .drop_duplicates(subset=pk, keep="last")
                .drop(columns="_uf")
            )
        else:
            data = data.drop_duplicates(subset=pk, keep="last")
        self.logger.warning(
            "    去重处理：%d 行 -> %d 行（删除 %d 行）",
            before, len(data), before - len(data),
        )
        return data

    def _resolve_schema(self, data: pd.DataFrame) -> Dict[str, str]:
        """解析 schema：加工层用 output_schema，接入层从 df 推断。"""
        if self.output_schema:
            return dict(self.output_schema)
        return infer_schema_from_df(data, self.primary_keys, self.type_overrides)

    # ===== etl_biz_date 水位表 =====

    def _next_after_biz_date(self) -> str:
        """水位次日（增量起点）。无水位则返回空（由 update 兜底为今天）。"""
        current = self._get_biz_date()
        if not current:
            return ""
        # 水位 +1 天（不区分交易日，由 get_data 自己判断）
        from datetime import datetime, timedelta

        dt = datetime.strptime(current, "%Y%m%d")
        return (dt + timedelta(days=1)).strftime("%Y%m%d")

    def _get_biz_date(self) -> Optional[str]:
        """读 etl_biz_date 水位。表/记录不存在返回 None。"""
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT biz_date FROM etl_biz_date WHERE table_name = :t"
                    ),
                    {"t": self.table_name},
                ).fetchone()
            return row[0] if row else None
        except Exception as e:
            self.logger.debug(f"读 etl_biz_date 失败（表可能未建）: {e}")
            return None

    def _set_biz_date(self, biz_date: str, rows: int = 0) -> None:
        """写/更新 etl_biz_date 水位。"""
        try:
            self._ensure_biz_date_table()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO etl_biz_date
                          (table_name, biz_date_col, biz_date, last_rows, status)
                        VALUES (:t, :c, :d, :r, 'ok')
                        ON DUPLICATE KEY UPDATE
                          biz_date = VALUES(biz_date),
                          biz_date_col = VALUES(biz_date_col),
                          last_rows = VALUES(last_rows),
                          last_updated = CURRENT_TIMESTAMP,
                          status = 'ok'
                        """
                    ),
                    {
                        "t": self.table_name,
                        "c": self.biz_date_col,
                        "d": biz_date,
                        "r": rows,
                    },
                )
            self.logger.info(f"{self.table_name} 水位更新 → {biz_date} ({rows} 行)")
        except Exception as e:
            self.logger.warning(f"写 etl_biz_date 失败（不阻塞）: {e}")

    @staticmethod
    def _ensure_biz_date_table() -> None:
        """确保 etl_biz_date 水位表存在（幂等）。"""
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS `etl_biz_date` (
                      `table_name` VARCHAR(100) PRIMARY KEY,
                      `biz_date_col` VARCHAR(30),
                      `biz_date` VARCHAR(30),
                      `last_updated` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      `last_rows` BIGINT DEFAULT 0,
                      `status` VARCHAR(20) DEFAULT 'ok',
                      INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
            )

    # ===== 工具 =====

    @staticmethod
    def _normalize_date(d: Optional[str]) -> str:
        """日期归一化为 yyyymmdd（去 -）。None 返回空串。"""
        if not d:
            return ""
        return d.replace("-", "")

    def _max_biz_date(self, df: pd.DataFrame) -> Optional[str]:
        """取 biz_date_col 列的最大值（yyyymmdd 字符串）。"""
        col = df[self.biz_date_col]
        # 兼容 date 对象 / 字符串 / datetime
        col_str = col.astype(str).str.replace("-", "")
        return col_str.max() if not col_str.empty else None

    # ===== 旧 API 兼容（迁移期保留，Step 9 删） =====

    def get_stock_list(self) -> List[str]:
        """获取所有股票代码（从 stock_basic）。"""
        query = "SELECT DISTINCT ts_code FROM stock_basic ORDER BY ts_code"
        try:
            stock_df = pd.read_sql(query, self.engine)
            return stock_df["ts_code"].tolist()
        except Exception as e:
            self.logger.error(f"获取股票列表失败: {e}")
            return []
