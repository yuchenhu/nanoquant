"""修复 schema 差异：删旧重建 market_sentiment 表、ALTER stock_daily、初始化缺表。

不能直接用 00_init_database.py，因为它不会删旧表重建。
所有 ALTER 操作做幂等检查，可安全重复执行。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_schema")

from sqlalchemy import text
from config.database import engine


def run_sql(sql: str, desc: str):
    """执行一条 SQL，打印结果。"""
    logger.info(desc)
    with engine.begin() as conn:
        conn.execute(text(sql))
    logger.info(f"  ✓ 完成")


def column_exists(table: str, column: str) -> bool:
    """检查表中是否存在某列。"""
    sql = text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl AND COLUMN_NAME = :col
    """)
    with engine.connect() as conn:
        return conn.execute(sql, {"tbl": table, "col": column}).scalar() > 0


def add_column_if_not_exists(table: str, column: str, col_type: str, after: str = None):
    """幂等添加列。"""
    if column_exists(table, column):
        logger.info(f"  {table}.{column} 已存在，跳过")
        return
    sql = f"ALTER TABLE `{table}` ADD COLUMN `{column}` {col_type}"
    if after:
        sql += f" AFTER `{after}`"
    run_sql(sql, f"ADD {table}.{column} {col_type}")


def drop_column_if_exists(table: str, column: str):
    """幂等删除列。"""
    if not column_exists(table, column):
        logger.info(f"  {table}.{column} 不存在，跳过")
        return
    run_sql(f"ALTER TABLE `{table}` DROP COLUMN `{column}`",
            f"DROP {table}.{column}")


# ============================================================
# 1. 删旧表（schema 完全重构，不可增量改）
# ============================================================
logger.info("=" * 60)
logger.info("STEP 1: 删旧表重建")

run_sql("DROP TABLE IF EXISTS panel_market_sentiment_monthly",
        "DROP panel_market_sentiment_monthly（旧版六支柱）")
run_sql("DROP TABLE IF EXISTS panel_market_sentiment_daily",
        "DROP panel_market_sentiment_daily（旧版）")

# ============================================================
# 2. ALTER panel_stock_daily：补缺失列 + 删废弃列
# ============================================================
logger.info("=" * 60)
logger.info("STEP 2: ALTER panel_stock_daily")

add_column_if_not_exists("panel_stock_daily", "net_mf_vol", "DOUBLE", "net_mf_amount")
add_column_if_not_exists("panel_stock_daily", "is_sz50", "BIGINT", "is_hs")
add_column_if_not_exists("panel_stock_daily", "is_zzqz", "BIGINT", "is_hldb")

drop_column_if_exists("panel_stock_daily", "is_zzhl")

# ============================================================
# 3. 建新表（通过 Calculator.ensure_table）
# ============================================================
logger.info("=" * 60)
logger.info("STEP 3: 建缺失表（ensure_table）")

from core.schema import ensure_table

from data.panel.market_sentiment_monthly import MarketSentimentMonthlyCalculator
from data.panel.market_sentiment_daily import MarketSentimentDailyCalculator
from data.panel.index_membership_monthly import IndexMembershipMonthlyCalculator
from data.panel.financial_statements_snapshot import FinancialStatementsSnapshotCalculator
from data.etl.loader import FundFactorProCalculator, FundNavCalculator

for name, cls in [
    ("panel_market_sentiment_monthly", MarketSentimentMonthlyCalculator),
    ("panel_market_sentiment_daily", MarketSentimentDailyCalculator),
    ("panel_index_membership_monthly", IndexMembershipMonthlyCalculator),
    ("panel_financial_statements_snapshot", FinancialStatementsSnapshotCalculator),
    ("fund_factor_pro", FundFactorProCalculator),
    ("fund_nav", FundNavCalculator),
]:
    inst = cls(engine=None)
    if inst.output_schema is None:
        logger.info(f"  {inst.table_name} output_schema=None（ETL接入层，运行时自动建），跳过")
        continue
    ensure_table(inst.table_name, inst.output_schema, primary_keys=inst.primary_keys)
    logger.info(f"  ✓ {inst.table_name}")

logger.info("=" * 60)
logger.info("Schema 修复完成！")
