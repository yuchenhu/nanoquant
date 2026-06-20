"""加工层计算运行脚本（Step 8）。

功能：调用 data/panel/ + data/factor/ + data/label/ 中的 Calculator，按依赖顺序计算。

用法：
    # 全量跑所有加工层（panel → factor → label）
    python scripts/run_compute.py

    # 指定 biz_date 区间
    python scripts/run_compute.py --start 20240101 --end 20240131

    # 只跑某一层
    python scripts/run_compute.py --layer panel
    python scripts/run_compute.py --layer factor
    python scripts/run_compute.py --layer label

    # 只跑指定 calculator
    python scripts/run_compute.py --only panel:stock_daily,factor:high_low_spread

    # 列出所有可用 calculator
    python scripts/run_compute.py --list
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_compute")


def _load_layer_registries() -> Dict[str, Dict[str, type]]:
    """加载三层注册表 {layer: {name: cls}}。"""
    registries: Dict[str, Dict[str, type]] = {}

    try:
        from data.panel import PANEL_CALCULATORS
        registries["panel"] = dict(PANEL_CALCULATORS)
    except Exception as e:
        logger.warning(f"加载 panel 注册表失败: {e}")
        registries["panel"] = {}

    try:
        from data.factor import CALCULATORS as FACTOR_CALCULATORS
        registries["factor"] = dict(FACTOR_CALCULATORS)
    except Exception as e:
        logger.warning(f"加载 factor 注册表失败: {e}")
        registries["factor"] = {}

    try:
        from data.label import CALCULATORS as LABEL_CALCULATORS
        registries["label"] = dict(LABEL_CALCULATORS)
    except Exception as e:
        logger.warning(f"加载 label 注册表失败: {e}")
        registries["label"] = {}

    return registries


def list_calculators() -> None:
    """列出所有可用 calculator。"""
    registries = _load_layer_registries()
    print("=" * 60)
    print("可用加工层 Calculator")
    print("=" * 60)
    for layer in ["panel", "factor", "label"]:
        reg = registries.get(layer, {})
        print(f"\n[{layer}] 共 {len(reg)} 个")
        for name, cls in reg.items():
            print(f"  {layer}:{name:35s} → {cls.table_name}")
    print("\n" + "=" * 60)


def run_compute(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    layer: Optional[str] = None,
    only: Optional[List[str]] = None,
) -> int:
    """运行加工层计算。"""
    registries = _load_layer_registries()

    # 决定要跑哪些 layer
    target_layers = [layer] if layer else ["panel", "factor", "label"]
    # 校验 layer 名
    for l in target_layers:
        if l not in registries:
            logger.error(f"未知 layer: {l}（可选: panel/factor/label）")
            return 1

    # 收集要跑的 (layer, name, cls)
    targets: List[Tuple[str, str, type]] = []
    for l in target_layers:
        for name, cls in registries[l].items():
            key = f"{l}:{name}"
            if only and key not in only:
                continue
            targets.append((l, name, cls))

    if not targets:
        logger.warning("没有匹配的 calculator 可运行")
        return 0

    logger.info(f"将运行 {len(targets)} 个 calculator（layers={target_layers}）")
    if start_date or end_date:
        logger.info(f"biz_date 区间: [{start_date or '水位次日'}, {end_date or '今天'}]")
    else:
        logger.info("biz_date 区间: 从水位次日续跑（增量）")

    success = 0
    failed = 0
    for l, name, cls in targets:
        try:
            logger.info(f"--- [{l}] 开始: {name} → {cls.table_name} ---")
            instance = cls()
            result = instance.update(start_date=start_date, end_date=end_date)
            rows = len(result) if result is not None else 0
            logger.info(f"--- [{l}] 完成: {name} ({rows} 行) ---")
            success += 1
        except Exception as e:
            logger.error(f"--- [{l}] 失败: {name}: {e} ---", exc_info=True)
            failed += 1

    logger.info("=" * 60)
    logger.info(f"加工层计算完成：成功 {success}，失败 {failed}")
    logger.info("=" * 60)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行加工层计算（panel → factor → label）")
    parser.add_argument("--start", type=str, help="起始 biz_date（yyyymmdd）")
    parser.add_argument("--end", type=str, help="结束 biz_date（yyyymmdd）")
    parser.add_argument("--layer", type=str, choices=["panel", "factor", "label"],
                        help="只跑某一层")
    parser.add_argument("--only", type=str,
                        help="只跑指定 calculator（逗号分隔，格式 layer:name，如 panel:stock_daily）")
    parser.add_argument("--list", action="store_true", help="列出所有可用 calculator 后退出")
    args = parser.parse_args()

    if args.list:
        list_calculators()
        return 0

    only = args.only.split(",") if args.only else None

    return run_compute(
        start_date=args.start,
        end_date=args.end,
        layer=args.layer,
        only=only,
    )


if __name__ == "__main__":
    sys.exit(main())
