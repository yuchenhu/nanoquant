"""panel_stock_daily 全量补数 2010-2026，逐年执行"""
import subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
venv_python = root / ".venv" / "Scripts" / "python.exe"
run_compute = root / "scripts" / "run_compute.py"

years = list(range(2010, 2027))
failed = []

for y in years:
    start = f"{y}0101"
    end = f"{y}1231" if y < 2026 else "20260630"
    print(f"==== {y} ({start}~{end}) ====", flush=True)
    r = subprocess.run(
        [str(venv_python), str(run_compute), "--start", start, "--end", end, "--only", "panel:stock_daily"],
        cwd=str(root),
    )
    if r.returncode != 0:
        print(f"  [FAIL] returncode={r.returncode}")
        failed.append(y)
    else:
        print(f"  [OK]")

print(f"\nDone: {len(years)-len(failed)}/{len(years)} success, failed: {failed}")
sys.exit(1 if failed else 0)
