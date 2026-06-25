"""scripts/: 可执行脚本集合。

脚本：
- 00_init_database.py: 初始化数据库（建元数据表 + 所有 Calculator 表）
- run_ingest.py: 运行 ETL 接入层（tushare → DB）
- run_compute.py: 运行加工层计算（panel → factor → label）
- gen_tushare_apis.py: 生成 config/tushare_apis.json（已废弃，保留历史）
"""
