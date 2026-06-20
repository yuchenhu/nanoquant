@echo off
REM Create log directory if not exists
if not exist "C:\Users\Lenovo\Documents\Quant\hyc_quant_project\logs" mkdir "C:\Users\Lenovo\Documents\Quant\hyc_quant_project\logs"

REM Generate log filename with date
for /f "tokens=1-3 delims=/-. " %%a in ('echo %date%') do set TODAY=%%a-%%b-%%c
set LOG_FILE=C:\Users\Lenovo\Documents\Quant\hyc_quant_project\logs\quant_%TODAY%.log

REM Run the task
cd /d C:\Users\Lenovo\Documents\Quant\hyc_quant_project\data\workflows
echo Started: %date% %time% >> %LOG_FILE%
python run_dag.py >> %LOG_FILE% 2>&1
echo Finished: %date% %time% >> %LOG_FILE%
echo. >> %LOG_FILE%