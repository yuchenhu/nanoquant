@echo off
REM nanoquant 每日任务：一键同步（API 接入层 -> 加工层 -> 缺口体检）
setlocal

set PROJECT_ROOT=%~dp0..
set LOG_DIR=%PROJECT_ROOT%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "tokens=1-3 delims=/-. " %%a in ('echo %date%') do set TODAY=%%a-%%b-%%c
set LOG_FILE=%LOG_DIR%\quant_%TODAY%.log

cd /d "%PROJECT_ROOT%"

echo Started: %date% %time% >> "%LOG_FILE%"
call "%PROJECT_ROOT%\scripts\py.bat" scripts\sync.py >> "%LOG_FILE%" 2>&1
echo Finished: %date% %time% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

endlocal
