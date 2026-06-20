"""兼容 shim：重导出顶层 core.preprocessing。

新代码请直接 `from core.preprocessing import ...`。
本文件在 Step 9 所有调用方迁移完后删除。
"""
from core.preprocessing import (  # noqa: F401
    mad_winsorize,
    standardize_factor,
    quantile_factor,
    rank_factor,
    neutralize_factor,
    orthogonalize_factor,
)
