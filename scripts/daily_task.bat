@echo off
REM nanoquant 每日任务
REM 用 %~dp0 推导项目根目录，避免硬编码绝对路径
setlocal

set PROJECT_ROOT=%~dp0..
set LOG_DIR=%PROJECT_ROOT%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 生成带日期的日志文件名
for /f "tokens=1-3 delims=/-. " %%a in ('echo %date%') do set TODAY=%%a-%%b-%%c
set LOG_FILE=%LOG_DIR%\quant_%TODAY%.log

REM 加载 .env（需要 python-dotenv 在脚本里处理，这里只切目录）
cd /d "%PROJECT_ROOT%"

echo Started: %date% %time% >> "%LOG_FILE%"
REM TODO: Step 8 将提供 run_ingest.py / run_compute.py，届时替换下面这行
python scripts\run_ingest.py >> "%LOG_FILE%" 2>&1
echo Finished: %date% %time% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

endlocal
