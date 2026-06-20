"""
平台配置文件
使用CSV管理数据源配置
"""

import os
import pandas as pd
from typing import Dict, Any, List
import json
from pathlib import Path
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== 平台基本信息 ====================
PLATFORM_CONFIG = {
    'name': 'HYC量化平台',
    'version': '1.0.0',
    'description': '基于Tushare的量化投资研究平台',
    'author': 'Hu Yuchen',
    'created_date': '2025-11-01'
}

# ==================== Tushare配置 ====================
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN', '06755e162d716f705e7bc8608803392020f4be245a6d5f815bde470e')
TUSHARE_BASE_URL = 'http://api.tushare.pro'

# 配置目录和文件路径
CONFIG_DIR = Path(__file__).parent
TUSHARE_API_JSON = CONFIG_DIR / 'tushare_api.json'

# ==================== 默认数据源配置 ====================
DEFAULT_API_CONFIG = {
    # 从最简单的几个开始，后续都在tushare_api.json中维护
    # 股票基本信息
    'stock_basic': {
        'api_name': 'stock_basic',
        'params': {
            'exchange': '',        # 交易所
            'list_status': 'L,D,P',# 上市状态: L上市 D退市 P暂停上市
            'market': '',          # 市场类别
            'fields': 'ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs'
        },
        'max_limit': 5000,
        'frequency': 'daily',      # 每日更新
        'write_mode': 'truncate',  # 清空后全量更新
        'table_name': 'stock_basic',
        'description': '股票基本信息'
    },
    
    # 交易日历
    'trade_cal': {
        'api_name': 'trade_cal',
        'params': {
            'exchange': '',        # 交易所 SSE上交所 SZSE深交所
            'start_date': '',      # 开始日期
            'end_date': '',        # 结束日期
            'is_open': '',         # 是否开市: 1开市 0休市
            'fields': 'exchange,cal_date,is_open,pretrade_date',
        },
        'max_limit': 10000,
        'frequency': 'monthly',    # 每月更新
        'write_mode': 'truncate',  # 清空后全量更新
        'table_name': 'trade_cal',
        'description': '交易日历'
    },
    
    # 每日ST
    'stock_st': {
        'api_name': 'stock_st',
        'params': {
            'ts_code': '',          # 股票代码
            'trade_date': '',       # 交易日期
            'start_date': '',       # 开始日期
            'end_date': '',         # 结束日期
            'fields': 'ts_code,name,trade_date,type,type_name',
        },
        'max_limit': 1000,
        'frequency': 'daily',       # 每日更新
        'write_mode': 'append',     
        'table_name': 'stock_st',
        'description': 'ST股票信息'
    },
    
    # 每日停复牌
    'suspend_d': {
        'api_name': 'suspend_d',
        'params': {
            'ts_code': '',          # 股票代码
            'trade_date': '',       # 交易日期
            'start_date': '',       # 开始日期
            'end_date': '',         # 结束日期
            'suspend_type': '',     # 停复牌类型
            'fields': 'ts_code,trade_date,suspend_timing,suspend_type',
        },
        'max_limit': 5000,
        'frequency': 'daily',       # 每日更新
        'write_mode': 'append',    
        'table_name': 'suspend',
        'description': '每日停复牌信息'
    },
    
    # 日线行情
    'daily': {
        'api_name': 'daily',
        'params': {
            'ts_code': '',         # 股票代码
            'trade_date': '',      # 交易日期(YYYYMMDD)
            'start_date': '',      # 开始日期
            'end_date': '',        # 结束日期
            'fields': 'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount',
        },
        'max_limit': 5000,
        'frequency': 'daily',      # 每日更新
        'write_mode': 'append',    
        'table_name': 'stock_daily',
        'description': '日线行情数据'
    },

    # 复权因子
    'adj_factor': {
        'api_name': 'adj_factor',
        'params': {
            'ts_code': '',
            'trade_date': '',
            'start_date': '',
            'end_date': '',
            'fields': 'ts_code,trade_date,adj_factor',
        },

        'max_limit': 5000,
        'frequency': 'daily',
        'write_mode': 'append',    
        'table_name': 'adj_factor',
        'description': '复权因子数据'
    },
    
    # 每日指标
    'daily_basic': {
        'api_name': 'daily_basic',
        'params': {
            'ts_code': '',
            'trade_date': '',
            'start_date': '',
            'end_date': '',
            'fields': 'ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,\
            total_share,float_share,free_share,total_mv,circ_mv',
        },
        'max_limit': 5000,
        'frequency': 'daily',
        'write_mode': 'append',    
        'table_name': 'stock_daily_basic',
        'description': '每日指标数据'
    },
}

def save_default_api_config():
    """将默认配置保存到JSON文件"""
    try:
        with open(TUSHARE_API_JSON, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_API_CONFIG, f, indent=2, ensure_ascii=False)
        logger.info(f"默认配置已保存到: {TUSHARE_API_JSON}")
        return True
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return False

def load_api_config() -> Dict[str, Dict]:
    """从JSON文件加载数据源配置"""
    # 如果JSON文件不存在，创建默认配置
    if not TUSHARE_API_JSON.exists():
        logger.info("配置文件不存在，创建默认配置")
        save_default_data_sources()
        return DEFAULT_API_CONFIG.copy()
    
    try:
        with open(TUSHARE_API_JSON, 'r', encoding='utf-8') as f:
            tushare_api = json.load(f)
        logger.info(f"从 {TUSHARE_API_JSON} 加载了 {len(tushare_api)} 个数据源配置")
        return tushare_api
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}，使用默认配置")
        return DEFAULT_API_CONFIG.copy()

# 初始化时加载配置
API_CONFIG = load_api_config()