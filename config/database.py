"""数据库 engine + 通用读写函数。

从 data/config/database.py 提升，移除依赖 table_schemas.sql 的 create_tables
（schema-as-code 后建表由 core/schema.py 自动管理）。

保留兼容：旧代码 `from data.config.database import *` 仍可用（data/config/database.py
已改为 shim 重导出本模块）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.mysql import insert

from config.settings import settings

logger = logging.getLogger(__name__)

# ==================== Engine ====================

def get_engine():
    """获取数据库引擎（pool_pre_ping 避免长连接断开）。"""
    return create_engine(
        settings.db_url,
        pool_pre_ping=True,
        echo=False,
    )


# 全局 engine（旧代码依赖 `from config.database import engine`）
engine = get_engine()

# 旧代码用的 DB_CONFIG 别名（部分模块直接引用）
DB_CONFIG = {
    "host": settings.db_host,
    "port": settings.db_port,
    "user": settings.db_user,
    "password": settings.db_password,
    "database": settings.db_database,
    "charset": settings.db_charset,
}

# 旧代码用的表结构缓存
TABLE_SCHEMAS: Dict[str, Dict[str, str]] = {}


# ==================== 核心读写函数 ====================

def execute_sql(sql: str, params: Optional[Dict] = None) -> pd.DataFrame:
    """执行 SQL 语句（SQLAlchemy 2.0 风格）。

    - 返回有结果集的 SELECT 时返回 DataFrame
    - 无结果集（DDL/DML）时返回空 DataFrame
    """
    with engine.connect() as conn:
        with conn.begin():
            result = conn.execute(text(sql), params or {})
            if result.returns_rows:
                return pd.DataFrame(result.fetchall(), columns=result.keys())
            return pd.DataFrame()


def upsert_data(table_name: str, data: pd.DataFrame, engine=engine) -> int:
    """UPSERT：用 MySQL ON DUPLICATE KEY UPDATE。

    要求表有主键；无主键的表会跳过并告警。
    """
    if data.empty:
        logger.warning(f"表 {table_name} 没有数据需要 UPSERT")
        return 0

    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        table_columns = [col.name for col in table.columns]
        primary_keys = [col.name for col in table.primary_key.columns]

        if not primary_keys:
            logger.warning(f"表 {table_name} 没有主键，无法进行 UPSERT")
            return 0

        valid_columns = [col for col in data.columns if col in table_columns]
        if not valid_columns:
            logger.error(f"表 {table_name} 的数据列与表结构不匹配")
            return 0

        data = data[valid_columns]
        records = data.to_dict("records")
        success_count = 0

        with engine.connect() as conn:
            with conn.begin():
                for record in records:
                    try:
                        stmt = insert(table).values(**record)
                        update_dict = {
                            k: v for k, v in record.items() if k not in primary_keys
                        }
                        stmt = stmt.on_duplicate_key_update(**update_dict)
                        result = conn.execute(stmt)
                        success_count += result.rowcount
                    except Exception as e:
                        logger.error(f"插入记录失败: {e}")
                        logger.debug(f"失败数据: {record}")
                        continue

        logger.info(f"表 {table_name} UPSERT 完成: {success_count} 行")
        return success_count

    except Exception as e:
        logger.error(f"SQLAlchemy Core UPSERT 失败: {e}")
        return 0


def save_to_database(
    df: pd.DataFrame,
    table_name: str,
    write_mode: str = "append",
    engine=engine,
) -> bool:
    """将 DataFrame 保存到数据库。

    write_mode:
    - truncate: TRUNCATE 后 append（全量刷新小表）
    - upsert:   ON DUPLICATE KEY UPDATE（按主键幂等）
    - append:   直接追加（不幂等，慎用）
    """
    if df is None or df.empty:
        logger.warning("数据为空，跳过保存")
        return False

    try:
        if write_mode == "truncate":
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                df.to_sql(name=table_name, con=conn, if_exists="append", index=False)
        elif write_mode == "upsert":
            upsert_data(table_name, df, engine=engine)
        else:  # append
            df.to_sql(name=table_name, con=engine, if_exists="append", index=False)

        logger.info(f"数据保存成功: {table_name} ({len(df)} 行, 模式: {write_mode})")
        return True
    except Exception as e:
        logger.error(f"数据保存失败 {table_name}: {e}")
        return False


# ==================== 表结构查询（旧代码兼容） ====================

def get_table_info(table_name: str) -> Dict[str, Any]:
    """获取表信息（列名 + 行数）。"""
    columns_info = execute_sql(f"DESCRIBE {table_name}")
    count_result = execute_sql(f"SELECT COUNT(*) as row_count FROM {table_name}")
    return {
        "table_name": table_name,
        "columns": columns_info["Field"].tolist() if not columns_info.empty else [],
        "row_count": count_result.iloc[0, 0] if not count_result.empty else 0,
    }


def get_table_schema(table_name: str) -> Optional[Dict[str, str]]:
    """获取指定表的表结构（简化类型）。"""
    if table_name in TABLE_SCHEMAS:
        return TABLE_SCHEMAS[table_name]

    try:
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name)
        schema: Dict[str, str] = {}
        for column in columns:
            col_type = str(column["type"])
            if "INT" in col_type:
                schema[column["name"]] = "int"
            elif "FLOAT" in col_type or "DECIMAL" in col_type or "DOUBLE" in col_type:
                schema[column["name"]] = "float"
            elif "DATE" in col_type or "TIME" in col_type:
                schema[column["name"]] = "date"
            elif "BOOL" in col_type:
                schema[column["name"]] = "bool"
            else:
                schema[column["name"]] = "string"
        TABLE_SCHEMAS[table_name] = schema
        return schema
    except Exception as e:
        logger.error(f"获取表结构失败 {table_name}: {e}")
        return None


def get_all_table_schemas() -> Dict[str, Dict[str, str]]:
    """获取数据库中所有表的表结构。"""
    try:
        inspector = inspect(engine)
        all_schemas: Dict[str, Dict[str, str]] = {}
        for table_name in inspector.get_table_names():
            schema = get_table_schema(table_name)
            if schema:
                all_schemas[table_name] = schema
        return all_schemas
    except Exception as e:
        logger.error(f"获取所有表结构失败: {e}")
        return {}


def clear_table_data(table_name: str) -> int:
    """清空表数据（保留表结构）。"""
    try:
        execute_sql(f"DELETE FROM {table_name}")
        logger.info(f"清空表数据: {table_name}")
        return 0
    except Exception as e:
        logger.error(f"清空表数据失败 {table_name}: {e}")
        return 0


def optimize_tables() -> None:
    """优化所有表。"""
    tables = execute_sql("SHOW TABLES")
    for table_name in tables.iloc[:, 0]:
        try:
            execute_sql(f"OPTIMIZE TABLE {table_name}")
        except Exception as e:
            logger.warning(f"优化表失败 {table_name}: {e}")


# ==================== 旧函数占位（保持 import 兼容） ====================
# create_tables / initialize_database / drop_all_tables / clear_all_test_data /
# test_database_functionality 等旧函数已移除（依赖 table_schemas.sql 或交互式输入）。
# 如需建表，用 scripts/00_init_database.py（Step 8 提供）+ core/schema.py 自动建表。
