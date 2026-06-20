"""Schema-as-code：从 DataFrame 推断 schema + 生成 DDL + 自动演化。

设计（见 CLAUDE.md 2.6）：
- 接入层：自动推断。表不存在 → infer_schema_from_df + ensure_table 建表；
  表存在 → evolve_schema 比对列差异，加列自动 ALTER，删列/改类型只告警不动。
- 加工层：手写 output_schema dict，ensure_table_from_schema 建表。

类型推断约定：
- object → VARCHAR(50)；列名含 _date / date_ → DATE
- int64 → BIGINT
- float64 → DOUBLE
- bool → TINYINT(1)
- datetime64 → DATETIME

tushare schema 变更策略：
| 场景     | 处理                  |
| 加列     | ALTER TABLE ADD COLUMN|
| 删列     | 不删，保留旧列         |
| 改列类型 | 日志告警，不自动改     |
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd
from sqlalchemy import inspect, text

from config.database import engine

logger = logging.getLogger(__name__)


# ==================== 类型推断 ====================

# pandas dtype → MySQL 类型
_DTYPE_TO_MYSQL: Dict[str, str] = {
    "int64": "BIGINT",
    "Int64": "BIGINT",
    "float64": "DOUBLE",
    "Float64": "DOUBLE",
    "bool": "TINYINT(1)",
    "boolean": "TINYINT(1)",
    "datetime64[ns]": "DATETIME",
    "datetime64[ns, UTC]": "DATETIME",
    "object": "VARCHAR(50)",
    "string": "VARCHAR(50)",
}


def infer_mysql_type(col_name: str, dtype_str: str, override: Optional[str] = None) -> str:
    """推断单列的 MySQL 类型。

    override 优先（Calculator 的 type_overrides 字典）。
    """
    if override:
        return override

    # 日期列特殊处理（tushare 日期是 yyyymmdd 字符串，入库转 DATE）
    lname = col_name.lower()
    if "_date" in lname or lname.startswith("date_") or lname == "trade_date" or lname == "ann_date":
        return "DATE"

    return _DTYPE_TO_MYSQL.get(dtype_str, "VARCHAR(50)")


def infer_schema_from_df(
    df: pd.DataFrame,
    primary_keys: Optional[list[str]] = None,
    type_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """从 DataFrame 推断 {列名: MySQL 类型}。

    primary_keys 中的列会被强制设为 NOT NULL（建表时主键自动 NOT NULL）。
    """
    type_overrides = type_overrides or {}
    schema: Dict[str, str] = {}
    for col in df.columns:
        dtype_str = str(df[col].dtype)
        schema[col] = infer_mysql_type(col, dtype_str, type_overrides.get(col))
    return schema


# ==================== DDL 生成 ====================

def generate_create_table_sql(
    table_name: str,
    schema: Dict[str, str],
    primary_keys: Optional[list[str]] = None,
    if_not_exists: bool = True,
) -> str:
    """生成 CREATE TABLE 语句（MySQL 方言）。"""
    primary_keys = primary_keys or []
    cols_sql = []
    for col_name, col_type in schema.items():
        nullable = "" if col_name in primary_keys else " NULL"
        cols_sql.append(f"  `{col_name}` {col_type}{nullable}")
    if primary_keys:
        pk_cols = ", ".join(f"`{c}`" for c in primary_keys)
        cols_sql.append(f"  PRIMARY KEY ({pk_cols})")
    exists_kw = "IF NOT EXISTS " if if_not_exists else ""
    return (
        f"CREATE TABLE {exists_kw}`{table_name}` (\n"
        + ",\n".join(cols_sql)
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )


# ==================== 建表 / 演化 ====================

def table_exists(table_name: str) -> bool:
    """表是否存在。"""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def get_existing_columns(table_name: str) -> Dict[str, str]:
    """获取已存在表的 {列名: 类型字符串}。"""
    inspector = inspect(engine)
    cols: Dict[str, str] = {}
    for col in inspector.get_columns(table_name):
        cols[col["name"]] = str(col["type"])
    return cols


def ensure_table(
    table_name: str,
    schema: Dict[str, str],
    primary_keys: Optional[list[str]] = None,
) -> None:
    """确保表存在：不存在则建，存在则演化（加列）。"""
    if not table_exists(table_name):
        ddl = generate_create_table_sql(table_name, schema, primary_keys)
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info(f"建表 {table_name}（{len(schema)} 列）")
        return

    # 表已存在 → 演化
    evolve_schema(table_name, schema)


def evolve_schema(table_name: str, new_schema: Dict[str, str]) -> None:
    """比对 new_schema 与库表列，按策略演化。

    - new_schema 多的列 → ALTER TABLE ADD COLUMN
    - new_schema 少的列 → 不删（保留旧列）
    - 类型不一致 → 告警，不动
    """
    existing = get_existing_columns(table_name)
    existing_lower = {k.lower(): k for k in existing}

    for col_name, col_type in new_schema.items():
        # 列名大小写不敏感比对（MySQL 默认不区分）
        actual_col = existing_lower.get(col_name.lower())
        if actual_col is None:
            # 加列
            ddl = f"ALTER TABLE `{table_name}` ADD COLUMN `{col_name}` {col_type} NULL"
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(f"表 {table_name} 加列: {col_name} {col_type}")
            _log_schema_change(table_name, "add_column", col_name, None, col_type)
        else:
            # 类型比对（简单子串匹配，避免 VARCHAR(50) vs VARCHAR(50) 的细节差异）
            existing_type = existing[actual_col].upper()
            new_type_upper = col_type.upper()
            if new_type_upper not in existing_type and existing_type not in new_type_upper:
                logger.warning(
                    f"表 {table_name} 列 {col_name} 类型不一致: "
                    f"库={existing_type}, 新={new_type_upper}（不自动改，需人工迁移）"
                )
                _log_schema_change(
                    table_name, "type_mismatch", col_name, existing_type, new_type_upper
                )


def _log_schema_change(
    table_name: str,
    change_type: str,
    column_name: str,
    old_value: Optional[str],
    new_value: Optional[str],
) -> None:
    """写 etl_schema_log 留痕（表不存在则跳过，不阻塞主流程）。"""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS `etl_schema_log` (
                      `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                      `table_name` VARCHAR(100) NOT NULL,
                      `change_type` VARCHAR(30) NOT NULL,
                      `column_name` VARCHAR(100),
                      `old_value` VARCHAR(200),
                      `new_value` VARCHAR(200),
                      `detected_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      INDEX idx_table (table_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO `etl_schema_log`
                      (table_name, change_type, column_name, old_value, new_value)
                    VALUES (:t, :c, :col, :o, :n)
                    """
                ),
                {
                    "t": table_name,
                    "c": change_type,
                    "col": column_name,
                    "o": old_value,
                    "n": new_value,
                },
            )
    except Exception as e:
        logger.debug(f"写 etl_schema_log 失败（不阻塞）: {e}")


# ==================== 日期列转换 ====================

def convert_date_columns(df: pd.DataFrame, schema: Dict[str, str]) -> pd.DataFrame:
    """把 schema 中 DATE 类型的列从 yyyymmdd 字符串转成 datetime.date。

    tushare 日期是 yyyymmdd 字符串，入库前需转 DATE。
    """
    df = df.copy()
    for col_name, col_type in schema.items():
        if col_name not in df.columns:
            continue
        if col_type.upper() == "DATE":
            df[col_name] = pd.to_datetime(df[col_name], format="%Y%m%d", errors="coerce").dt.date
    return df
