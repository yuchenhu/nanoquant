"""数据库 engine + 通用读写函数。

schema-as-code 后建表由 core/schema.py 自动管理（ensure_table / evolve_schema）。
水位表 etl_biz_date + 留痕表 etl_schema_log 由 scripts/00_init_database.py 创建。
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


def overwrite_by_partition(
    df: pd.DataFrame,
    table_name: str,
    partition_col: str,
    engine=engine,
    primary_keys: Optional[list] = None,
) -> int:
    """INSERT OVERWRITE 语义（dataworks 风格）：先删本批分区，再批量 append。

    幂等保证：删除维度(partition_col) == 取数维度。重跑 = 删该批分区全部 + 写该批分区全部。
    不脏保证：删除粒度(partition_col) ⊇ 写入粒度，旧数据全清后再写，不残留、不交叉。

    分区键示例：
      - 行情类   partition_col = "trade_date"（按交易日覆盖）
      - 财务类   partition_col = "end_date"（按报告期覆盖）
      - 分红     partition_col = "ex_date"（按除权日覆盖）

    去重护栏（primary_keys 非空时启用）：
      - 落库前按主键去重，避免数据源偶发重复导致 to_sql append 主键冲突报错
      - 若有 update_flag 列：保留 update_flag 最大的版本（留修正版）
      - 否则：保留最后一条
      - 关键：去掉重复行时打 WARNING 并显式列出被删的主键值，便于人工核查数据源
        （不静默吞掉，符合"业务有意义的重复不能被无声 drop"的要求）

    在同一事务内 DELETE + INSERT，失败回滚，不会出现"删了没写"的空窗。
    """
    if df is None or df.empty:
        logger.warning(f"{table_name} overwrite 跳过：空数据")
        return 0

    if partition_col not in df.columns:
        raise ValueError(
            f"{table_name} overwrite 失败：分区列 {partition_col} 不在数据列中"
        )

    # ===== 去重护栏：按主键去重 + 显式告警 =====
    if primary_keys:
        pk_in_df = [c for c in primary_keys if c in df.columns]
        if pk_in_df:
            dup_mask = df.duplicated(subset=pk_in_df, keep=False)
            n_dup = int(dup_mask.sum())
            if n_dup > 0:
                # 显式列出被判定为重复的主键组合（最多 20 组，防日志爆炸）
                dup_keys = (
                    df.loc[dup_mask, pk_in_df]
                    .drop_duplicates()
                    .head(20)
                    .to_dict("records")
                )
                logger.warning(
                    "!!! %s 发现 %d 行重复主键（主键=%s），数据源可能异常，请人工核查 !!!",
                    table_name, n_dup, pk_in_df,
                )
                for k in dup_keys:
                    logger.warning("    重复主键: %s", k)
                if len(dup_keys) == 20:
                    logger.warning("    （仅显示前 20 组重复主键，可能还有更多）")

                # 去重：有 update_flag 则留最大版本，否则留最后一条
                before = len(df)
                if "update_flag" in df.columns:
                    df = df.copy()
                    df["_uf"] = pd.to_numeric(
                        df["update_flag"], errors="coerce"
                    ).fillna(0)
                    df = (
                        df.sort_values(pk_in_df + ["_uf"])
                        .drop_duplicates(subset=pk_in_df, keep="last")
                        .drop(columns="_uf")
                    )
                else:
                    df = df.drop_duplicates(subset=pk_in_df, keep="last")
                logger.warning(
                    "    去重处理：%d 行 → %d 行（删除 %d 行）",
                    before, len(df), before - len(df),
                )

    # 本批涉及的分区值（去空 + 去重）。保留原始类型（date/str），由 SQLAlchemy 绑定
    partitions = pd.Series(df[partition_col].dropna().unique()).tolist()
    if not partitions:
        logger.warning(f"{table_name} overwrite 跳过：分区值全空")
        return 0

    with engine.begin() as conn:
        # 1) 删除本批分区的存量数据（参数化 IN，防注入 + 类型安全）
        placeholders = ", ".join(f":p{i}" for i in range(len(partitions)))
        params = {f"p{i}": v for i, v in enumerate(partitions)}
        conn.execute(
            text(f"DELETE FROM {table_name} WHERE {partition_col} IN ({placeholders})"),
            params,
        )
        # 2) 批量写入（executemany，比逐行 upsert 快几十倍）
        df.to_sql(name=table_name, con=conn, if_exists="append", index=False)

    logger.info(
        f"{table_name} overwrite 完成: {len(partitions)} 个分区, {len(df)} 行 "
        f"({partition_col} 覆盖)"
    )
    return len(df)


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


# ==================== 建表入口 ====================
# 如需建表，用 scripts/00_init_database.py + core/schema.py 自动建表。
