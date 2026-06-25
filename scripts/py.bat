@echo off
REM nanoquant venv python launcher: cmd 直接开干, 免激活虚拟环境
REM 用 %~dp0 推导项目根, 固定调用 .venv 里的 python, 把后面所有参数原样透传
REM 用法示例:
REM   scripts\py.bat scripts\backfill_years.py --from-year 2021 --to-year 2026
REM   scripts\py.bat scripts\sync.py --start 20210101 --end 20211231
REM   scripts\py.bat scripts\sync.py
setlocal
REM 控制台切 UTF-8 代码页 + 让 Python 用 UTF-8 读写 stdio, 避免中文 print/log 乱码
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] venv python not found: "%VENV_PY%"
    echo Run from project root, make sure .venv exists.
    exit /b 1
)
cd /d "%~dp0.."
"%VENV_PY%" %*
endlocal
