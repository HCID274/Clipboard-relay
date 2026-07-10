@echo off
setlocal

set "TASK_NAME=ClipboardRelayAgent"

schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 exit /b %errorlevel%

echo Uninstalled scheduled task "%TASK_NAME%".
