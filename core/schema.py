"""Schema-as-code：从 DataFrame 推断 schema + 生成 DDL + 自动演化。

设计（见 CLAUDE.md 2.6）：
- 接入层：自动推断。表不存在 → infer_schema_from_df + ensure_table 建表；
  表存在 → evolve_schema 比对列差异，加列自动 ALTER，删列/改类型只告警不动。
- 加工层：手写 output_schema dict，ensure_table_from_schema 建表。

类型推断约定（_infer_col_type，按优先级）：
- type_overrides 指定 → 用指定值
- 列名含 _date / date_ → DATE
- dtype 明确（float64/int64/bool/datetime）→ 直接映射（float64→DOUBLE）
- object 列：desc→TEXT；name/*_name/长文本→VARCHAR(255)；
  其余字符串（ts_code/*_code/*_flag/枚举状态码）→统一 VARCHAR(32)
- 其余 object 列数值探测：全空或全数值→DOUBLE，含字符串→VARCHAR(32)
- 数值列统一 DOUBLE（不用 DECIMAL，避免大额溢出 + 省空间收益<1%）
- 字符串只两档：名称/长文本 VARCHAR(255)，其余 VARCHAR(32)；desc→TEXT

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
    "object": "VARCHAR(255)",
    "string": "VARCHAR(255)",
}

# 加工层 output_schema 简写 → MySQL 类型（手写 schema 用简写，建表时转）
_SCHEMA_SHORTHAND_TO_MYSQL: Dict[str, str] = {
    "string": "VARCHAR(255)",
    "str": "VARCHAR(255)",
    "text": "TEXT",
    "int": "BIGINT",
    "integer": "BIGINT",
    "bigint": "BIGINT",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "decimal": "DOUBLE",
    "bool": "TINYINT(1)",
    "boolean": "TINYINT(1)",
    "date": "DATE",
    "datetime": "DATETIME",
    "timestamp": "DATETIME",
}


def _normalize_col_type(col_name: str, col_type: str) -> str:
    """把 schema 里的类型描述统一转成 MySQL 类型。

    支持两种写法：
    - 简写（加工层 output_schema）：string/int/float/bool/date/datetime → MySQL 类型
    - MySQL 原生类型（接入层推断）：VARCHAR(50)/BIGINT/DOUBLE/... → 原样返回

    日期列名（含 _date / trade_date / ann_date）若类型是 string，自动转 DATE。
    """
    if not col_type:
        return "VARCHAR(255)"

    ct_lower = col_type.lower().strip()

    # 日期列名特殊处理（tushare 日期是 yyyymmdd 字符串，入库转 DATE）
    lname = col_name.lower()
    is_date_col = (
        "_date" in lname
        or lname.startswith("date_")
        or lname in ("trade_date", "ann_date", "snapshot_date")
    )
    if is_date_col and ct_lower in ("string", "str", "varchar", "varchar(50)"):
        return "DATE"

    # 简写优先
    if ct_lower in _SCHEMA_SHORTHAND_TO_MYSQL:
        return _SCHEMA_SHORTHAND_TO_MYSQL[ct_lower]

    # 已经是 MySQL 类型（含括号或全大写），原样返回
    return col_type


def infer_mysql_type(col_name: str, dtype_str: str, override: Optional[str] = None) -> str:
    """推断单列的 MySQL 类型（仅凭 dtype，不看数据）。

    保留旧签名供外部调用；infer_schema_from_df 改用 _infer_col_type（带数据探测）。
    override 优先（Calculator 的 type_overrides 字典）。
    """
    if override:
        return override

    # 日期列特殊处理（tushare 日期是 yyyymmdd 字符串，入库转 DATE）
    lname = col_name.lower()
    if "_date" in lname or lname.startswith("date_") or lname == "trade_date" or lname == "ann_date":
        return "DATE"

    return _DTYPE_TO_MYSQL.get(dtype_str, "VARCHAR(255)")


# ===== 已知字符串列（即使全空也保持字符串类型，不被数值探测误判） =====
# 短字符串类（代码/枚举/状态码/标志位）→ VARCHAR(32)
_STR32_COLS = {
    "ts_code", "con_code", "index_code", "symbol",
    "report_type", "comp_type", "end_type", "div_proc", "suspend_type",
    "type", "type_name", "list_status", "is_hs",
    "is_pub", "is_new", "src", "market", "exchange", "curr_type",
    "update_flag",
}
# 名称/长文本类 → VARCHAR(255)
_NAME_COLS = {
    "name", "fullname", "enname", "cnspell", "area", "industry",
    "act_name", "act_ent_type", "benchmark", "management", "custodian",
    "invest_type", "fund_type", "publisher", "category", "index_type",
    "weight_rule", "l1_name", "l2_name", "l3_name", "industry_name",
    "parent_code", "industry_code", "l1_code", "l2_code", "l3_code",
}


def _infer_col_type(col_name: str, series: "pd.Series", override: Optional[str] = None) -> str:
    """推断单列 MySQL 类型（带数据探测，解决全空数值列被误判 VARCHAR 的问题）。

    字符串只两档：名称/长文本 → VARCHAR(255)，其余（含 flag/code/枚举）→ VARCHAR(32)。
    desc 超长 → TEXT。数值列统一 DOUBLE。
    """
    if override:
        return override

    lname = col_name.lower()

    # 1. 日期列名 → DATE
    if "_date" in lname or lname.startswith("date_") or lname in ("trade_date", "ann_date"):
        return "DATE"

    # 2. dtype 已是明确类型（数值/布尔/时间）→ 直接映射，不再探测
    dtype_str = str(series.dtype)
    if dtype_str in _DTYPE_TO_MYSQL and dtype_str not in ("object", "string"):
        return _DTYPE_TO_MYSQL[dtype_str]

    # 3. object/string 列：按列名语义判断字符串列
    if lname == "desc" or lname == "suspend_timing":
        return "TEXT"             # 描述长文本（suspend_timing 早年存停牌原因，可能超长）
    if lname in _NAME_COLS or lname.endswith("_name"):
        return "VARCHAR(255)"     # 名称/长文本
    if (
        lname in _STR32_COLS
        or lname.endswith("_code")
        or lname.endswith("_flag")
    ):
        return "VARCHAR(32)"      # 代码/枚举/标志位等短字符串

    # 4. 未知 object 列：数值探测（解决财务罕见科目全空 → 应为 DOUBLE）
    non_null = series.dropna()
    if len(non_null) == 0:
        return "DOUBLE"           # 全空列默认数值（财务字段绝大多数是数值）
    converted = pd.to_numeric(non_null, errors="coerce")
    if converted.notna().all():
        return "DOUBLE"           # 所有非空值都能转成数字 → 数值列
    return "VARCHAR(32)"          # 含真实字符串的未知短列 → VARCHAR(32)


def infer_schema_from_df(
    df: pd.DataFrame,
    primary_keys: Optional[list[str]] = None,
    type_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """从 DataFrame 推断 {列名: MySQL 类型}。

    primary_keys 中的列会被强制设为 NOT NULL（建表时主键自动 NOT NULL）。
    object 列会做数值探测：全空或全数值 → DOUBLE，避免缺数列被误判 VARCHAR。
    """
    type_overrides = type_overrides or {}
    schema: Dict[str, str] = {}
    for col in df.columns:
        schema[col] = _infer_col_type(col, df[col], type_overrides.get(col))
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
        mysql_type = _normalize_col_type(col_name, col_type)
        nullable = "" if col_name in primary_keys else " NULL"
        cols_sql.append(f"  `{col_name}` {mysql_type}{nullable}")
    if primary_keys:
        pk_cols = ", ".join(f"`{c}`" for c in primary_keys)
        cols_sql.append(f"  PRIMARY KEY ({pk_cols})")
    exists_kw = "IF NOT EXISTS " if if_not_exists else ""
    return (
        f"CREATE TABLE {exists_kw}`{table_name}` (\n"
        + ",\n".join(cols_sql)
        # ROW_FORMAT=DYNAMIC：VARCHAR/TEXT 超长部分存溢出页，行内只留指针，
        # 避免财务宽表（150+ 列）触发 InnoDB 行大小 65535 字节上限（错误 1118）
        + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC"
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
    """把 schema 中 DATE 类型的列从 yyyymmdd / yyyy-mm-dd 字符串转成 datetime.date。

    tushare/接入层日期是 yyyymmdd；加工层输出可能是 yyyy-mm-dd（按约定）。
    不用固定 format：pd.to_datetime 自动探测两种格式。
    """
    df = df.copy()
    for col_name, col_type in schema.items():
        if col_name not in df.columns:
            continue
        if col_type.upper() == "DATE":
            df[col_name] = pd.to_datetime(df[col_name], errors="coerce").dt.date
    return df
