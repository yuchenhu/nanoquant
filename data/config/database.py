from sqlalchemy import create_engine, inspect, text
from sqlalchemy import Table, MetaData
from sqlalchemy.dialects.mysql import insert
import pandas as pd
import re
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

# A股数据库配置
# 密码从环境变量读取，默认留空；本地用 .env + python-dotenv 加载
# 注意：旧版本曾在此硬编码密码，已移除，请尽快轮换该 MySQL 密码
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_DATABASE', 'stock'),
    'charset': 'utf8mb4'
}

# 创建数据库引擎
def get_engine():
    """获取数据库引擎"""
    database_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        f"?charset={DB_CONFIG['charset']}"
    )
    return create_engine(database_url, pool_pre_ping=True, echo=False)

engine = get_engine()

# 全局变量存储表结构
TABLE_SCHEMAS = {}

# ==================== 核心数据库函数 ====================

def execute_sql(sql: str, params: Dict = None) -> pd.DataFrame:
    """
    执行SQL语句 - 适配 SQLAlchemy 2.0
    
    参数:
    - sql: SQL语句
    - params: 参数字典
    
    返回: 包含结果的DataFrame
    """
    with engine.connect() as conn:
        with conn.begin():
            result = conn.execute(text(sql), params or {})
            
            if result.returns_rows:
                return pd.DataFrame(result.fetchall(), columns=result.keys())
            return pd.DataFrame()

def create_tables():
    """
    从SQL文件创建所有表（极简版）
    """
    logger.info("开始创建数据库表...")
    
    # 读取SQL文件
    sql_file = Path(__file__).parent / 'table_schemas.sql'
    
    if not sql_file.exists():
        logger.error(f"SQL文件不存在: {sql_file}")
        return
    
    try:
        with open(sql_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 移除注释和空行，然后按分号分割语句
        content = re.sub(r'--.*$', '', content, flags=re.MULTILINE)  # 移除单行注释
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)  # 移除多行注释
        statements = [stmt.strip() for stmt in content.split(';') if stmt.strip()]
        
        logger.info(f"找到 {len(statements)} 条建表语句")
        
        # 执行所有建表语句
        for stmt in statements:
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
                logger.info(f"✅ 表创建成功")
            except Exception as e:
                logger.error(f"❌ 表创建失败: {e}")
        
        logger.info("数据库表创建完成")
        
    except Exception as e:
        logger.error(f"创建表过程出错: {e}")


def upsert_data(table_name: str, data: pd.DataFrame, engine=engine) -> int:
    """
    使用 SQLAlchemy Core 进行 UPSERT 操作
    使用 MySQL 方言的 insert().on_duplicate_key_update()
    
    Args:
        table_name: 表名
        data: 要插入的数据
        engine: 数据库引擎（默认使用全局engine）
        
    Returns:
        成功处理的行数
    """
    if data.empty:
        logger.warning(f"表 {table_name} 没有数据需要UPSERT")
        return 0
    
    try:
        # 使用反射获取表对象
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        
        # 获取表结构信息
        table_columns = [col.name for col in table.columns]
        primary_keys = [col.name for col in table.primary_key.columns]
        
        if not primary_keys:
            logger.warning(f"表 {table_name} 没有主键，无法进行UPSERT")
            return 0
        
        # 过滤数据列，只保留表中存在的列
        valid_columns = [col for col in data.columns if col in table_columns]
        if not valid_columns:
            logger.error(f"表 {table_name} 的数据列与表结构不匹配")
            return 0
        
        data = data[valid_columns]
        records = data.to_dict('records')
        
        success_count = 0
        
        with engine.connect() as conn:
            with conn.begin():
                for record in records:
                    try:
                        # 使用 MySQL 方言的 insert
                        stmt = insert(table).values(**record)
                        
                        # 构建更新部分
                        update_dict = {}
                        for key, value in record.items():
                            if key not in primary_keys:
                                update_dict[key] = value
                        
                        # 使用 MySQL 的 ON DUPLICATE KEY UPDATE
                        stmt = stmt.on_duplicate_key_update(**update_dict)
                        
                        result = conn.execute(stmt)
                        success_count += result.rowcount
                        
                    except Exception as e:
                        logger.error(f"插入记录失败: {e}")
                        logger.debug(f"失败数据: {record}")
                        continue
        
        logger.info(f"表 {table_name} UPSERT完成: {success_count} 行")
        return success_count
        
    except Exception as e:
        logger.error(f"SQLAlchemy Core UPSERT失败: {e}")

def save_to_database(df: pd.DataFrame, table_name: str, write_mode: str = 'append', engine=engine) -> bool:
    """
    将DataFrame保存到数据库
    
    Args:
        df: 要保存的数据
        table_name: 表名
        write_mode: 写入模式 ('truncate', 'upsert', 'append')
        engine: 数据库引擎（默认使用全局engine）
        
    Returns:
        bool: 是否成功
    """
    if df is None or df.empty:
        logger.warning("数据为空，跳过保存")
        return False
    
    try:
        if write_mode == 'truncate':
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                df.to_sql(name=table_name, con=conn, if_exists='append', index=False)
        elif write_mode == 'upsert':
            upsert_data(table_name, df, engine=engine)
        else:  # append 模式
            df.to_sql(name=table_name, con=engine, if_exists='append', index=False)
        
        logger.info(f"数据保存成功: {table_name} ({len(df)} 行, 模式: {write_mode})")
        return True
        
    except Exception as e:
        logger.error(f"数据保存失败 {table_name}: {e}")
        return False

def optimize_tables():
    """优化数据表性能"""
    tables = execute_sql("SHOW TABLES")
    
    for table_name in tables.iloc[:, 0]:
        try:
            execute_sql(f"OPTIMIZE TABLE {table_name}")
            logger.info(f"✓ 优化表: {table_name}")
        except Exception as e:
            logger.warning(f"优化表失败 {table_name}: {e}")

def get_table_info(table_name: str) -> Dict[str, Any]:
    """获取表信息"""
    # 获取表结构
    columns_info = execute_sql(f"DESCRIBE {table_name}")
    
    # 获取行数
    count_result = execute_sql(f"SELECT COUNT(*) as row_count FROM {table_name}")
    
    return {
        'table_name': table_name,
        'columns': columns_info['Field'].tolist() if not columns_info.empty else [],
        'row_count': count_result.iloc[0, 0] if not count_result.empty else 0
    }

def clear_table_data(table_name: str) -> int:
    """
    清空表数据（保留表结构）
    
    参数:
    - table_name: 表名
    
    返回: 删除的行数
    """
    try:
        result = execute_sql(f"DELETE FROM {table_name}")
        logger.info(f"✓ 清空表数据: {table_name}")
        return result
    except Exception as e:
        logger.error(f"清空表数据失败 {table_name}: {e}")
        return 0

def clear_all_test_data():
    """
    清空所有测试数据（保留表结构）
    用于测试完成后清理环境
    """
    tables = execute_sql("SHOW TABLES")
    
    for table_name in tables.iloc[:, 0]:
        clear_table_data(table_name)
    
    logger.info("✓ 所有测试数据已清空")

def drop_all_tables(confirm: bool = True) -> bool:
    """
    删除当前数据库中的所有表（极简版）
    
    Args:
        confirm: 是否需要确认操作
        
    Returns:
        bool: 是否成功
    """
    try:
        # 获取所有表名
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if not tables:
            logger.info("数据库中没有表")
            return True
        
        # 显示将要删除的表
        logger.warning(f"准备删除 {len(tables)} 个表:")
        for table in tables:
            logger.warning(f"  - {table}")
        
        # 确认操作
        if confirm:
            user_input = input(f"确认删除以上 {len(tables)} 个表？此操作不可逆！(输入 'Y' 确认): ")
            if user_input != 'Y':
                logger.info("操作已取消")
                return False
        
        # 执行删除
        with engine.connect() as conn:
            # 禁用外键检查
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            
            # 删除所有表
            for table in tables:
                conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
                logger.info(f"已删除表: {table}")
            
            # 重新启用外键检查
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            conn.commit()
        
        logger.info(f"✅ 成功删除 {len(tables)} 个表")
        return True
        
    except Exception as e:
        logger.error(f"删除表失败: {e}")
        return False

def initialize_database():
    """初始化数据库（一次性调用）"""
    create_tables()
    logger.info("✓ 数据库初始化完成")

def get_table_schema(table_name: str) -> Optional[Dict[str, str]]:
    """
    获取指定表的表结构
    
    Args:
        table_name: 表名
    
    Returns:
        表结构字典 {列名: 数据类型}
    """
    # 如果已经缓存过，直接返回
    if table_name in TABLE_SCHEMAS:
        return TABLE_SCHEMAS[table_name]
    
    try:
        # 使用SQLAlchemy的inspect获取表结构
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name)
        
        schema = {}
        for column in columns:
            col_name = column['name']
            col_type = str(column['type'])
            
            # 简化数据类型
            if 'INT' in col_type:
                schema[col_name] = 'int'
            elif 'FLOAT' in col_type or 'DECIMAL' in col_type or 'DOUBLE' in col_type:
                schema[col_name] = 'float'
            elif 'DATE' in col_type or 'TIME' in col_type:
                schema[col_name] = 'date'
            elif 'BOOL' in col_type:
                schema[col_name] = 'bool'
            else:
                schema[col_name] = 'string'
        
        # 缓存表结构
        TABLE_SCHEMAS[table_name] = schema
        logger.info(f"获取表结构成功: {table_name} ({len(schema)} 列)")
        return schema
        
    except Exception as e:
        logger.error(f"获取表结构失败 {table_name}: {e}")
        return None

def get_all_table_schemas() -> Dict[str, Dict[str, str]]:
    """
    获取数据库中所有表的表结构
    
    Returns:
        所有表结构的字典 {表名: {列名: 数据类型}}
    """
    try:
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        
        all_schemas = {}
        for table_name in table_names:
            schema = get_table_schema(table_name)
            if schema:
                all_schemas[table_name] = schema
        
        logger.info(f"获取所有表结构完成: {len(all_schemas)} 张表")
        return all_schemas
        
    except Exception as e:
        logger.error(f"获取所有表结构失败: {e}")
        return {}

# ==================== 极简测试函数 ====================

def test_database_functionality():
    """
    极简测试函数
    测试核心功能，完成后自动清理数据
    """
    print("=== 极简数据库功能测试 ===")
    
    try:
        # 1. 初始化数据库
        print("1. 初始化数据库...")
        initialize_database()
        
        # 2. 测试UPSERT功能
        print("2. 测试UPSERT功能...")
        
        # 准备测试数据
        test_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ', '000003.SZ'],
            'trade_date': ['20230101', '20230101', '20230101'],
            'open': [10.5, 20.3, 15.7],
            'high': [10.8, 20.8, 16.2],
            'low': [10.2, 19.8, 15.3],
            'close': [10.6, 20.5, 15.9],
            'vol': [1000000, 2000000, 1500000]
        })
        
        # 插入数据
        upsert_data('stock_daily', test_data)
        print("✓ 数据插入完成")
        
        # 3. 验证数据插入
        print("3. 验证数据插入...")
        result = execute_sql("SELECT COUNT(*) as row_count FROM stock_daily")
        row_count = result.iloc[0, 0] if not result.empty else 0
        print(f"✓ 表行数: {row_count}")
        
        # 4. 测试查询功能
        print("4. 测试查询功能...")
        query_result = execute_sql("SELECT ts_code, trade_date, close FROM stock_daily ORDER BY ts_code")
        print("查询结果:")
        print(query_result.to_string(index=False))
        
        # 5. 测试UPSERT更新
        print("5. 测试UPSERT更新...")
        update_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'trade_date': ['20230101', '20230101'],
            'close': [11.0, 21.0]  # 更新收盘价
        })
        
        upsert_data('stock_daily', update_data)
        print("✓ 数据更新完成")
        
        # 验证更新
        updated_result = execute_sql("SELECT ts_code, trade_date, close FROM stock_daily ORDER BY ts_code")
        print("更新后数据:")
        print(updated_result.to_string(index=False))
        
        # 6. 测试指数表
        print("6. 测试指数表...")
        index_data = pd.DataFrame({
            'ts_code': ['000001.SH', '000300.SH'],
            'trade_date': ['20230101', '20230101'],
            'close': [3200.5, 4200.3],
            'pct_chg': [1.5, 2.3]
        })
        
        upsert_data('index_daily', index_data)
        print("✓ 指数数据插入完成")
        
        # 7. 表信息查询
        print("7. 表信息查询...")
        for table in ['stock_daily', 'index_daily', 'index_daily_basic']:
            info = get_table_info(table)
            print(f"{table}: {info['row_count']} 行")
        
        # 8. 表优化测试
        print("8. 表优化测试...")
        optimize_tables()
        print("✓ 表优化完成")
        
        print("✓ 所有测试通过")
        
    finally:
        # 9. 清理测试数据（无论测试成功与否都会执行）
        print("9. 清理测试数据...")
        clear_all_test_data()
        
        # 验证数据已清空
        for table in ['stock_daily', 'index_daily']:
            result = execute_sql(f"SELECT COUNT(*) as cnt FROM {table}")
            count = result.iloc[0, 0] if not result.empty else 0
            print(f"{table} 清理后行数: {count}")
        
        print("✓ 测试环境清理完成")

if __name__ == "__main__":
    # 运行测试
    test_database_functionality()