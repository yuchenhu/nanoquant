"""Step 9 验收测试：废弃旧文件（Airflow DAG、table_schemas.sql、旧 data/config & data/utils shims、旧 data/sql、旧 ETL 三件套）。

验证：
1. data/config/ 目录已删除
2. data/utils/ 目录已删除
3. data/sql/ 目录已删除
4. data/workflows/ 目录已删除
5. data/etl/extractor.py / transformer.py / validator.py 已删除
6. table_schemas.sql 已删除
7. tests/test_step2_imports.py 已删除（验证 shim 的，shim 删了就失效）
8. 项目代码中无 data.config / data.utils / data.sql / data.workflows 引用
9. data/etl/ 只剩 base.py / loader.py / __init__.py
10. 现有 Step 3-8 测试仍通过
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_data_config_removed():
    """测试 1：data/config/ 目录已删除。"""
    assert not (ROOT / "data" / "config").exists(), "data/config/ 应已删除"
    print("[OK] data/config/ 已删除")


def test_data_utils_removed():
    """测试 2：data/utils/ 目录已删除。"""
    assert not (ROOT / "data" / "utils").exists(), "data/utils/ 应已删除"
    print("[OK] data/utils/ 已删除")


def test_data_sql_removed():
    """测试 3：data/sql/ 目录已删除。"""
    assert not (ROOT / "data" / "sql").exists(), "data/sql/ 应已删除"
    print("[OK] data/sql/ 已删除")


def test_data_workflows_removed():
    """测试 4：data/workflows/ 目录已删除（Airflow DAG）。"""
    assert not (ROOT / "data" / "workflows").exists(), "data/workflows/ 应已删除"
    print("[OK] data/workflows/ 已删除（Airflow DAG）")


def test_old_etl_modules_removed():
    """测试 5：data/etl/extractor.py / transformer.py / validator.py 已删除。"""
    for name in ["extractor.py", "transformer.py", "validator.py"]:
        assert not (ROOT / "data" / "etl" / name).exists(), f"data/etl/{name} 应已删除"
    print("[OK] data/etl/extractor.py / transformer.py / validator.py 已删除")


def test_table_schemas_sql_removed():
    """测试 6：table_schemas.sql 已删除。"""
    matches = list(ROOT.rglob("table_schemas.sql"))
    assert not matches, f"table_schemas.sql 应已删除，但仍存在: {matches}"
    print("[OK] table_schemas.sql 已删除")


def test_step2_test_removed():
    """测试 7：tests/test_step2_imports.py 已删除（验证 shim 的，shim 删了就失效）。"""
    assert not (ROOT / "tests" / "test_step2_imports.py").exists(), \
        "tests/test_step2_imports.py 应已删除（验证 shim 的，shim 删了就失效）"
    print("[OK] tests/test_step2_imports.py 已删除")


def test_no_legacy_imports_in_code():
    """测试 8：项目代码中无 data.config / data.utils / data.sql / data.workflows 引用。

    允许在 tests/test_step6/7/8 中出现（这些是断言"不应依赖"的字符串）。
    """
    ignore_dirs = {".userbase", ".git", "__pycache__"}
    legacy_patterns = [
        "from data.config", "from data.utils", "from data.sql", "from data.workflows",
        "import data.config", "import data.utils", "import data.sql", "import data.workflows",
    ]
    violations = []
    for py in ROOT.rglob("*.py"):
        # 跳过忽略目录
        if any(part in ignore_dirs for part in py.parts):
            continue
        # 跳过 test_step6/7/8（断言字符串）
        if py.name in ("test_step6_panel.py", "test_step7_factor_label.py", "test_step8_scripts.py"):
            continue
        # 跳过本测试自身（包含断言字符串）
        if py.name == "test_step9_cleanup.py":
            continue
        try:
            content = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in legacy_patterns:
            if pat in content:
                violations.append((py.relative_to(ROOT), pat))
    assert not violations, f"仍有旧路径引用: {violations}"
    print("[OK] 项目代码中无 data.config / data.utils / data.sql / data.workflows 引用")


def test_etl_dir_clean():
    """测试 9：data/etl/ 只剩 base.py / loader.py / __init__.py。"""
    etl_dir = ROOT / "data" / "etl"
    py_files = sorted(f.name for f in etl_dir.glob("*.py"))
    expected = ["__init__.py", "base.py", "loader.py"]
    assert py_files == expected, f"data/etl/ 应只剩 {expected}，实际 {py_files}"
    print(f"[OK] data/etl/ 只剩 {expected}")


def test_existing_step_tests_still_pass():
    """测试 10：现有 Step 3-8 测试仍通过。"""
    import subprocess
    import os
    test_files = [
        "tests/test_step3_pipeline.py",
        "tests/test_step4_tushare_apis.py",
        "tests/test_step5_etl.py",
        "tests/test_step6_panel.py",
        "tests/test_step7_factor_label.py",
        "tests/test_step8_scripts.py",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    failed = []
    for tf in test_files:
        if not (ROOT / tf).exists():
            continue
        result = subprocess.run(
            [sys.executable, tf],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            failed.append((tf, result.returncode, (result.stderr or result.stdout)[-500:]))
    assert not failed, f"以下测试失败: {failed}"
    print("[OK] Step 3-8 测试全部通过")


if __name__ == "__main__":
    print("=" * 60)
    print("Step 9 验收测试：废弃旧文件")
    print("=" * 60)
    test_data_config_removed()
    test_data_utils_removed()
    test_data_sql_removed()
    test_data_workflows_removed()
    test_old_etl_modules_removed()
    test_table_schemas_sql_removed()
    test_step2_test_removed()
    test_no_legacy_imports_in_code()
    test_etl_dir_clean()
    test_existing_step_tests_still_pass()
    print("=" * 60)
    print("所有验收测试通过 ✅")
    print("=" * 60)
