"""标的池配置（指数 / 未来 ETF）—— 接入层与下游策略层共用的"关注标的"单一事实源。

为什么单独抽一个 config：
- 接入层(data/etl/loader.py)需要"拉哪些指数"（含双版冗余代码，保证任何年份不漏数据）
- 下游(panel/策略层)需要"去重后的规范指数"（每个指数一个 canonical 代码）
- 两者来自同一份定义，改标的池只动这一个文件，不碰代码逻辑

────────────────────────────── 双版冗余说明 ──────────────────────────────
沪深300/500/1000 等指数，tushare 有沪(.SH/.CSI)和深(.SZ)两个代码，且
index_weight 成分权重的归属代码随年份变化（实测：沪深300 早年成分只在
399300.SZ、近年在 000300.SH）。所以：
  - 接入层：两个代码都拉（ALL_INDEX_CODES），保证任何年份成分穿透不缺数据
  - 下游  ：用 CODE_TO_CANONICAL 把 alt 代码归一到 canonical，去重后对齐时点成分
"""
from __future__ import annotations

from typing import Dict, List

# ============================================================================
# 指数池定义（唯一事实源）。每个指数：
#   key       = canonical 代码（下游统一用这个）
#   name      = 中文名
#   alt       = 同一指数的另一个代码（双版冗余），无则省略
# 增删指数只动这个字典。加双版指数前务必用 MCP 验早年+近年 index_weight 都能取到。
# ============================================================================
INDEX_POOL: Dict[str, Dict[str, dict]] = {
    # —— 宽基（大→小盘 + 科创/创业）——
    "broad": {
        "000016.SH": {"name": "上证50"},
        "000300.SH": {"name": "沪深300", "alt": "399300.SZ"},
        "000905.SH": {"name": "中证500", "alt": "399905.SZ"},
        "000906.SH": {"name": "中证800"},
        "000852.SH": {"name": "中证1000", "alt": "399852.SZ"},
        "932000.CSI": {"name": "中证2000"},
        "000688.SH": {"name": "科创50"},
        "399006.SZ": {"name": "创业板指"},
        "000985.CSI": {"name": "中证全指"},
    },
    # —— 风格（红利 / 价值 / 低波 / 质量）——
    "style": {
        "000922.CSI": {"name": "中证红利"},
        "930955.CSI": {"name": "中证红利低波动"},
        "931052.CSI": {"name": "红利低波100"},
        "000015.SH": {"name": "上证红利"},
        "000919.CSI": {"name": "300价值"},
        "000925.CSI": {"name": "基本面50"},
    },
}


# ===== 以下三个列表/映射由 INDEX_POOL 自动派生，请勿手工维护 =====

def _build():
    canonical: List[str] = []          # 去重后的规范代码（下游用）
    all_codes: List[str] = []          # 含双版的全部代码（接入层用）
    to_canonical: Dict[str, str] = {}  # alt/canonical 代码 → canonical 代码
    name_map: Dict[str, str] = {}      # canonical 代码 → 中文名
    for group in INDEX_POOL.values():
        for code, meta in group.items():
            canonical.append(code)
            all_codes.append(code)
            to_canonical[code] = code
            name_map[code] = meta["name"]
            alt = meta.get("alt")
            if alt:
                all_codes.append(alt)
                to_canonical[alt] = code   # 双版的 alt 归一到 canonical
    return canonical, all_codes, to_canonical, name_map


# 下游（panel / 策略层）用：每个指数唯一 canonical 代码（15 个）
CANONICAL_INDEX_CODES: List[str]
# 接入层（loader.py）用：含双版冗余的全部代码（18 个，保证不漏）
ALL_INDEX_CODES: List[str]
# 下游去重映射：alt 代码 → canonical 代码（如 399300.SZ → 000300.SH）
CODE_TO_CANONICAL: Dict[str, str]
# canonical 代码 → 中文名
INDEX_NAME: Dict[str, str]

CANONICAL_INDEX_CODES, ALL_INDEX_CODES, CODE_TO_CANONICAL, INDEX_NAME = _build()


def canonical(code: str) -> str:
    """把任意指数代码归一到 canonical（下游去重用）。未知代码原样返回。"""
    return CODE_TO_CANONICAL.get(code, code)
