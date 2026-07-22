"""数据库初始化脚本（Step 8）。

功能：
1. 测试数据库连接
2. 创建 etl_biz_date 水位表 + etl_schema_log 留痕表
3. 遍历所有 Calculator（etl/panel/factor/label），用 output_schema 自动建表
4. 打印建表结果摘要

用法：
    python scripts/00_init_database.py
    python scripts/00_init_database.py --dry-run  # 只打印不执行
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text

from config.database import engine
from core.schema import ensure_table, generate_create_table_sql

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("init_db")


def test_connection() -> bool:
    """测试数据库连接。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("数据库连接正常 ✓")
        return True
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return False


def create_meta_tables() -> None:
    """创建水位表 + schema 留痕表。"""
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
    logger.info("元数据表 etl_biz_date / etl_schema_log 已就绪")


def _collect_calculators() -> List[Tuple[str, str, str, Dict[str, str], List[str]]]:
    """收集所有 Calculator 的建表信息。

    返回 [(layer, name, table_name, output_schema, primary_keys), ...]
    """
    result: List[Tuple[str, str, str, Dict[str, str], List[str]]] = []

    # ===== ETL 接入层（22 个） =====
    try:
        from data.etl.loader import CALCULATORS as ETL_CALCULATORS
        for name, cls in ETL_CALCULATORS.items():
            instance = cls(engine=None)
            # 接入层 output_schema 可能为 None，由 df 推断（建表时跳过，运行时自动建）
            schema = instance.output_schema
            if schema is None:
                logger.debug(f"ETL {name} 无 output_schema（运行时自动建表），跳过")
                continue
            result.append((
                "etl", name, instance.table_name,
                dict(schema), list(cls.primary_keys),
            ))
    except Exception as e:
        logger.warning(f"加载 ETL calculators 失败（跳过）: {e}")

    # ===== Panel 层（7 个） =====
    try:
        from data.panel import PANEL_CALCULATORS
        for name, cls in PANEL_CALCULATORS.items():
            instance = cls(engine=None)
            schema = instance.output_schema
            if schema is None:
                logger.debug(f"Panel {name} 无 output_schema，跳过")
                continue
            result.append((
                "panel", name, instance.table_name,
                dict(schema), list(cls.primary_keys),
            ))
    except Exception as e:
        logger.warning(f"加载 Panel calculators 失败（跳过）: {e}")

    # ===== Factor 层（6 个） =====
    try:
        from data.factor import CALCULATORS as FACTOR_CALCULATORS
        for name, cls in FACTOR_CALCULATORS.items():
            instance = cls(engine=None)
            schema = instance.output_schema
            if schema is None:
                continue
            result.append((
                "factor", name, instance.table_name,
                dict(schema), list(cls.primary_keys),
            ))
    except Exception as e:
        logger.warning(f"加载 Factor calculators 失败（跳过）: {e}")

    # ===== Label 层（1 个） =====
    try:
        from data.label import CALCULATORS as LABEL_CALCULATORS
        for name, cls in LABEL_CALCULATORS.items():
            instance = cls(engine=None)
            schema = instance.output_schema
            if schema is None:
                continue
            result.append((
                "label", name, instance.table_name,
                dict(schema), list(cls.primary_keys),
            ))
    except Exception as e:
        logger.warning(f"加载 Label calculators 失败（跳过）: {e}")

    return result


def init_all_tables(dry_run: bool = False) -> None:
    """遍历所有 Calculator，用 output_schema 建表。"""
    calculators = _collect_calculators()
    logger.info(f"共 {len(calculators)} 个 Calculator 待建表")

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    created = 0
    skipped = 0
    for layer, name, table_name, schema, pks in calculators:
        if table_name in existing_tables:
            logger.debug(f"[{layer}] {table_name} 已存在，跳过建表")
            skipped += 1
            continue
        if dry_run:
            ddl = generate_create_table_sql(table_name, schema, pks)
            logger.info(f"[DRY-RUN][{layer}] {name} → {table_name} ({len(schema)} 列)")
            logger.debug(f"DDL:\n{ddl}")
        else:
            try:
                ensure_table(table_name, schema, pks)
                logger.info(f"[{layer}] {name} → 建表 {table_name} ({len(schema)} 列)")
                created += 1
            except Exception as e:
                logger.error(f"[{layer}] {name} → 建表失败 {table_name}: {e}")
    logger.info(f"建表完成：新建 {created}，已存在跳过 {skipped}")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化数据库（建元数据表 + 所有 Calculator 表）")
    parser.add_argument("--dry-run", action="store_true", help="只打印 DDL，不执行")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("nanoquant 数据库初始化")
    logger.info("=" * 60)

    if not test_connection():
        return 1

    create_meta_tables()
    init_all_tables(dry_run=args.dry_run)

    logger.info("=" * 60)
    logger.info("初始化完成 ✓")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
