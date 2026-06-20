"""兼容 shim：重导出顶层 config.database。

新代码请直接 `from config.database import ...`。
本文件在 Step 9 所有调用方迁移完后删除。
"""
from sqlalchemy import text  # noqa: F401  (旧 loader.py 直接 from data.config.database import text)

from config.database import (  # noqa: F401
    DB_CONFIG,
    TABLE_SCHEMAS,
    engine,
    execute_sql,
    save_to_database,
    upsert_data,
    get_engine,
    get_table_info,
    get_table_schema,
    get_all_table_schemas,
    clear_table_data,
    optimize_tables,
)
