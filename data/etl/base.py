"""接入层 Calculator 基类（tushare 拉数 + 三类增量策略）。

设计（CLAUDE.md 2.1 / 2.5 / 硬约束 1/3/7/8）：
- TushareClient: 单例 tushare pro 客户端（token 从 config.settings 读）
- load_api_config(): 从 config/tushare_apis.json 加载 22 个接口配置
- TushareCalculatorMixin: 提供 fetch_tushare(api_name, **params)（分页 + 重试）
- 三个中间基类（继承策略基类 + Mixin），实现 fetch_one_period：
    TushareByTradeDateCalculator: fetch_one_period(trade_date=...) → pro.api(trade_date=...)
    TushareByAnnDateCalculator:   fetch_one_period(start_ann_date=..., end_ann_date=...) → pro.api(start_date=..., end_date=...)
    TushareFullRefreshCalculator: fetch_one_period() → pro.api(**params)

22 个具体 Calculator（data/etl/loader.py）只声明 config_key，继承对应中间基类。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config.settings import settings
from pipeline.incremental.by_ann_date import ByAnnDateCalculator
from pipeline.incremental.by_trade_date import ByTradeDateCalculator
from pipeline.incremental.full_refresh import FullRefreshCalculator

logger = logging.getLogger(__name__)

# ===== tushare 客户端（单例，懒加载） =====
_pro_client = None


def get_pro_client():
    """获取 tushare pro 客户端（单例）。token 从 settings 读。"""
    global _pro_client
    if _pro_client is None:
        import tushare as ts
        token = settings.tushare_token
        if not token:
            raise RuntimeError(
                "TUSHARE_TOKEN 未配置，请在 .env 设置（见 .env.example）"
            )
        ts.set_token(token)
        _pro_client = ts.pro_api()
        logger.info("tushare pro 客户端已初始化")
    return _pro_client


# ===== config/tushare_apis.json 加载 =====
_CONFIG_CACHE: Optional[Dict[str, Dict]] = None
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "tushare_apis.json"


def load_api_config() -> Dict[str, Dict]:
    """加载 config/tushare_apis.json（带缓存）。"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        if not _CONFIG_PATH.exists():
            raise FileNotFoundError(f"tushare 接口配置不存在: {_CONFIG_PATH}")
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _CONFIG_CACHE = json.load(f)
        logger.info("从 %s 加载 %d 个 tushare 接口配置", _CONFIG_PATH.name, len(_CONFIG_CACHE))
    return _CONFIG_CACHE


def get_api_config(config_key: str) -> Dict[str, Any]:
    """取单个接口配置。"""
    cfg = load_api_config()
    if config_key not in cfg:
        raise KeyError(f"tushare_apis.json 无接口 {config_key}")
    return cfg[config_key]


# ===== tushare 拉数（分页 + 重试） =====
def fetch_tushare(
    api_name: str,
    params: Dict[str, Any],
    fields: str = "",
    max_retries: int = 3,
    page_limit: Optional[int] = None,
) -> pd.DataFrame:
    """调 tushare pro.api_name(**params)，自动分页 + 重试。

    - api_name: tushare Python 包方法名（如 daily / income_vip）
    - params: 查询参数（不含 offset/limit，由本函数管理）
    - fields: 指定返回字段（逗号分隔字符串），空则用接口默认
    - page_limit: 单页条数，None 则从 config 读
    """
    pro = get_pro_client()
    api_func = getattr(pro, api_name)

    # 单页条数：优先 page_limit，其次 config params.limit，默认 5000
    if page_limit is None:
        page_limit = params.get("limit", 5000)

    # 构造查询参数（去掉空值，避免 tushare 报错）
    base_params: Dict[str, Any] = {}
    for k, v in params.items():
        if k in ("limit", "offset"):
            continue
        if v is None or v == "":
            continue
        base_params[k] = v
    if fields:
        base_params["fields"] = fields

    all_frames: List[pd.DataFrame] = []
    offset = 0
    retry = 0

    while True:
        page_params = dict(base_params)
        page_params["limit"] = page_limit
        page_params["offset"] = offset
        try:
            df = api_func(**page_params)
        except Exception as e:
            retry += 1
            logger.warning(
                "%s offset=%d 第 %d 次失败: %s", api_name, offset, retry, e
            )
            if retry >= max_retries:
                logger.error("%s 达最大重试次数，停止", api_name)
                break
            time.sleep(retry * 2)
            continue

        if df is None or df.empty:
            break

        all_frames.append(df)
        logger.info("%s offset=%d 取到 %d 行", api_name, offset, len(df))

        if len(df) < page_limit:
            break  # 最后一页

        offset += page_limit
        retry = 0
        time.sleep(0.2)  # 避免 tushare 频率限制

    if not all_frames:
        return pd.DataFrame()

    result = pd.concat(all_frames, ignore_index=True)
    logger.info("%s 共取到 %d 行", api_name, len(result))
    return result


# ===== Mixin: 提供 fetch_tushare 给策略基类用 =====
class TushareCalculatorMixin:
    """tushare 接入层 Mixin：提供 config_key + fetch_tushare。

    子类声明：
        config_key: str  # 对应 config/tushare_apis.json 的 key
    """

    config_key: str = ""

    @classmethod
    def _cfg(cls) -> Dict[str, Any]:
        if not cls.config_key:
            raise ValueError(f"{cls.__name__} 必须声明 config_key")
        return get_api_config(cls.config_key)

    @property
    def api_name(self) -> str:
        return self._cfg()["api_name"]

    @property
    def fields(self) -> str:
        return self._cfg().get("fields", "")

    @property
    def default_params(self) -> Dict[str, Any]:
        return dict(self._cfg().get("params", {}))

    def fetch_tushare(self, **params) -> pd.DataFrame:
        """调 tushare（用本接口的 api_name + fields + 合并 params）。"""
        merged = dict(self.default_params)
        merged.update(params)
        return fetch_tushare(self.api_name, merged, fields=self.fields)

    def process_data(self, data: pd.DataFrame, **params) -> pd.DataFrame:
        """接入层 process_data：只做 NaN/NaT → None（MySQL 兼容），不做类型转换。

        类型转换由 core.schema.convert_date_columns + save_to_database 自动处理。
        """
        if data is None or data.empty:
            return data
        return data.replace([float("nan"), float("inf"), float("-inf"), pd.NaT], None)


# ===== 三个中间基类 =====
class TushareByTradeDateCalculator(TushareCalculatorMixin, ByTradeDateCalculator):
    """行情类接入层基类（by_trade_date）。

    子类声明 config_key 即可。fetch_one_period(trade_date=...) 调 tushare。
    """

    def fetch_one_period(self, trade_date: str, **params) -> Optional[pd.DataFrame]:
        return self.fetch_tushare(trade_date=trade_date, **params)


class TushareByAnnDateCalculator(TushareCalculatorMixin, ByAnnDateCalculator):
    """财务类接入层基类（by_ann_date）。

    子类声明 config_key 即可。fetch_one_period(start_ann_date=..., end_ann_date=...)
    映射到 tushare 的 start_date/end_date（公告日区间）。
    """

    def fetch_one_period(
        self, start_ann_date: str, end_ann_date: str, **params
    ) -> Optional[pd.DataFrame]:
        return self.fetch_tushare(
            start_date=start_ann_date, end_date=end_ann_date, **params
        )


class TushareFullRefreshCalculator(TushareCalculatorMixin, FullRefreshCalculator):
    """基础信息类接入层基类（full_refresh）。

    子类声明 config_key 即可。fetch_one_period() 调 tushare 全量拉取。

    特殊接口（stock_basic 遍历 list_status、index_member_all 遍历 is_new、
    index_daily 遍历 index_codes、index_weight 遍历 index_codes + 月份区间）
    覆盖 fetch_one_period 实现自定义逻辑。
    """

    def fetch_one_period(self, **params) -> Optional[pd.DataFrame]:
        return self.fetch_tushare(**params)
