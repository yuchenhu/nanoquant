"""nanoquant 一致性 lint 脚本（Loop Engineering 落地）。

设计原则（LOOP_ENGINEERING.md S2）：
- 不依赖固定行号（用符号 grep + 语义断言）
- 区分定义 vs 调用（查调用，不查死代码定义残留）
- tokenize 过滤注释 + docstring（避免误报）
- err 阻塞（退出码 1），warn 不阻塞（退出码 0）

三类 check：
1. schedule_compute 依赖一致性：grep 代码中 FROM panel_/factor_/label_ vs depends_on
2. write_mode=upsert 残留：项目规则"已废弃 upsert"，但代码大面积违规
3. 策略层固定阈值：违反"零固定阈值"原则（CLAUDE.md 原则 2.2）

用法：
    python scripts/_lint_consistency.py

退出码：
    0 = 全绿（可有 warn）
    1 = 有 err
"""
from __future__ import annotations

import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


# ================================================================
# 辅助：过滤 Python 代码中的注释 + docstring
# ================================================================

def strip_py_noncode(text: str) -> str:
    """过滤 # 注释 + 多行 docstring 内容，保留代码部分与行号。

    用 tokenize 标准库精确识别，不用简单正则（# 开头才过滤会漏 docstring）。
    """
    lines = text.splitlines(keepends=True)
    blank_rows: set[int] = set()
    comment_cuts: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                comment_cuts[tok.start[0] - 1] = tok.start[1]
            elif tok.type == tokenize.STRING:
                start_r, end_r = tok.start[0] - 1, tok.end[0] - 1
                if start_r != end_r:  # 跨行 docstring
                    for i in range(start_r, end_r + 1):
                        blank_rows.add(i)
    except tokenize.TokenizeError:
        pass
    out = []
    for i, ln in enumerate(lines):
        if i in blank_rows:
            out.append("\n" if ln.endswith("\n") else "")
        elif i in comment_cuts:
            col = comment_cuts[i]
            out.append(ln[:col] + ("\n" if ln.endswith("\n") else ""))
        else:
            out.append(ln)
    return "".join(out)


def read_code_stripped(path: Path) -> str:
    """读 Python 文件，返回去掉注释/docstring 后的纯代码。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return strip_py_noncode(text)


# ================================================================
# Check 1: schedule_compute 依赖一致性
# ================================================================

def check_schedule_deps() -> None:
    """grep 代码中 FROM panel_/factor_/label_ 读取的表名
    vs schedule_compute.json 的 depends_on 逐项比对。

    漏声明 = 拓扑不保证顺序 = 静默脏数据（项目规则 Loop 2 验证门 9）。
    """
    print("[Check 1] schedule_compute 依赖一致性...")
    schedule_path = PROJECT_ROOT / "pipeline" / "schedule_compute.json"
    if not schedule_path.exists():
        err(f"schedule_compute.json 不存在: {schedule_path}")
        return

    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))

    # 收集每个 task 的 depends_on 声明
    # schedule 结构：{"daily": [task_dict, ...], "monthly": [...]}
    # task_dict: {"task_id": str, "depends_on": [str, ...], ...}
    task_deps: dict[str, list[str]] = {}
    for freq, freq_tasks in schedule.items():
        if not isinstance(freq_tasks, list):
            continue
        for t in freq_tasks:
            if not isinstance(t, dict):
                continue
            tid = t.get("task_id")
            if tid:
                task_deps[tid] = list(t.get("depends_on", []))

    # 扫描 data/panel+factor+label/ 下所有 .py，grep FROM panel_/factor_/label_
    data_dir = PROJECT_ROOT / "data"
    pattern = re.compile(
        r'FROM\s+(panel_|factor_|label_)([a-zA-Z0-9_]+)',
        re.IGNORECASE,
    )
    # task_id 通常 == 文件名去掉 .py（如 stock_daily_panel.py -> stock_daily_panel）
    # 也可能 == 类名小写（少数），先按文件名匹配
    for py_file in sorted(data_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        code = read_code_stripped(py_file)
        refs: Set[str] = set()
        for m in pattern.finditer(code):
            table = f"{m.group(1)}{m.group(2)}"
            refs.add(table)
        if not refs:
            continue

        # 推断此文件对应的 task_id：文件名去掉 .py
        task_id = py_file.stem
        # 也尝试类名风格（如 stock_daily_panel -> StockDailyPanelCalculator 的 task）
        # schedule 里 task_id 通常是 snake_case 文件名
        declared = set()
        if task_id in task_deps:
            declared = set(task_deps[task_id])
        else:
            # 文件名可能在 schedule 中作为 class 路径的一部分，找 task_id 包含文件名
            for tid, deps in task_deps.items():
                if task_id in tid or tid in task_id:
                    declared = set(deps)
                    break

        # 检查每个引用的表是否在 depends_on 中（直接表名 or 去前缀的 task_id）
        for ref in sorted(refs):
            # depends_on 里可能是表名（panel_stock_daily）或 task_id（stock_daily_panel）
            ref_no_prefix = ref.split("_", 1)[1] if "_" in ref else ref
            ref_variants = {ref, ref_no_prefix, ref.replace("panel_", "").replace("factor_", "").replace("label_", "")}
            if not (declared & ref_variants):
                warn(
                    f"{py_file.relative_to(PROJECT_ROOT)} 引用 {ref} 但 "
                    f"task_id={task_id} 的 depends_on={sorted(declared)} 未声明。"
                    f" 漏声明=拓扑不保证顺序=静默脏数据风险。"
                )


# ================================================================
# Check 2: write_mode=upsert 残留
# ================================================================

def check_upsert_residual() -> None:
    """项目规则"已废弃 upsert"，但代码大面积违规。

    已知豁免：
    - core/calculator.py / data/*/base.py：基类默认值，子类应覆盖（warn）
    - tests/：历史验收测试，非生产代码（warn）
    - 实际子类 Calculator：违规（err）
    - signals/generator.py 的 save_to_database 调用：违规（err，已标 TODO）
    """
    print("[Check 2] write_mode=upsert 残留...")
    upsert_pattern = re.compile(r'write_mode\s*=\s*["\']upsert["\']')

    # 基类文件（warn，不阻塞）
    base_files = {
        Path("core/calculator.py"),
        Path("data/panel/base.py"),
        Path("data/factor/base.py"),
        Path("data/label/base.py"),
    }
    # 测试文件（warn）
    test_dir = PROJECT_ROOT / "tests"

    for py_file in sorted(PROJECT_ROOT.rglob("*.py")):
        if "/.venv/" in str(py_file) or "\\.venv\\" in str(py_file):
            continue
        if "__pycache__" in str(py_file):
            continue
        rel = py_file.relative_to(PROJECT_ROOT)
        code = read_code_stripped(py_file)
        matches = upsert_pattern.findall(code)
        if not matches:
            continue
        n = len(matches)

        if rel in base_files:
            warn(
                f"{rel}: write_mode=upsert 作为基类默认值出现 {n} 处。"
                f" 项目规则已废弃 upsert，建议改默认值为 overwrite 并要求子类显式声明 partition_col。"
            )
        elif test_dir in py_file.parents or str(rel).startswith("tests/"):
            warn(
                f"{rel}: write_mode=upsert 出现 {n} 处（测试文件，非生产）。"
                f" 历史验收测试，按 N5.2 梳理时同步处理。"
            )
        else:
            # 生产代码子类 / signals 等：err
            err(
                f"{rel}: write_mode=upsert 出现 {n} 处，违反项目规则'已废弃 upsert'。"
                f" 改为 overwrite + partition_col（参考 data/panel/market_sentiment_monthly.py）。"
            )


# ================================================================
# Check 3: 策略层固定阈值
# ================================================================

def check_fixed_thresholds() -> None:
    """策略层禁止固定阈值（CLAUDE.md 原则 2.2：用滚动百分位）。

    扫描 portfolio/ + signals/ + backtest/ 下的硬编码数字赋值给阈值参数。
    """
    print("[Check 3] 策略层固定阈值...")
    # 阈值参数名模式
    threshold_param_pattern = re.compile(
        r'(vol_threshold|max_drawdown|stop_loss|circuit_breaker)\s*[=:]\s*(?:float\s*\()?\s*(0\.\d+|\d+\.?\d*)',
        re.IGNORECASE,
    )
    # 函数签名默认值
    threshold_default_pattern = re.compile(
        r'(vol_threshold|max_drawdown|stop_loss|circuit_breaker)\s*:\s*float\s*=\s*(0\.\d+|\d+\.?\d*)',
        re.IGNORECASE,
    )

    strategy_dirs = [PROJECT_ROOT / "portfolio", PROJECT_ROOT / "signals", PROJECT_ROOT / "backtest"]
    for d in strategy_dirs:
        if not d.exists():
            continue
        for py_file in sorted(d.rglob("*.py")):
            if py_file.name == "__init__.py":
                continue
            code = read_code_stripped(py_file)
            rel = py_file.relative_to(PROJECT_ROOT)
            for m in threshold_default_pattern.finditer(code):
                param, val = m.group(1), m.group(2)
                warn(
                    f"{rel}: 参数 {param}={val} 是固定阈值。"
                    f" CLAUDE.md 原则 2.2 要求滚动百分位（如 vol_percentile=0.75 用 5 年滚动 rank）。"
                    f" 当前为 MVP 简化，留 N5.6 单独 spec 修复。"
                )
            for m in threshold_param_pattern.finditer(code):
                # 跳过函数签名默认值（已在上面匹配）
                if ":" in m.group(0) and "float" in m.group(0):
                    continue
                param, val = m.group(1), m.group(2)
                # 跳过 0.0（合理默认，不算阈值）
                if val in ("0", "0.0"):
                    continue
                warn(
                    f"{rel}: 参数 {param}={val} 是固定阈值。"
                    f" 建议改滚动百分位（CLAUDE.md 原则 2.2）。"
                )


# ================================================================
# main
# ================================================================

def main() -> int:
    print("=" * 60)
    print("nanoquant 一致性 lint")
    print("=" * 60)

    check_schedule_deps()
    check_upsert_residual()
    check_fixed_thresholds()

    print("\n" + "=" * 60)
    if warns:
        print(f"[WARN] {len(warns)} 条警告（不阻塞）：")
        for w in warns:
            print(f"  - {w}")
    if errors:
        print(f"\n[ERR] {len(errors)} 条错误（阻塞）：")
        for e in errors:
            print(f"  - {e}")
        print("\nFAILED -- 修复后再继续")
        return 1
    print("\nPASSED -- 全绿（可有 warn）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
