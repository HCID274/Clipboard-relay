@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHONW=%PROJECT_DIR%\.venv\Scripts\pythonw.exe"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "AGENT=%PROJECT_DIR%\agent.py"
set "TASK_NAME=ClipboardRelayAgent"
set "CLIPBOARD_RELAY_PROJECT_DIR=%PROJECT_DIR%"

if not exist "%PYTHONW%" (
  echo Missing "%PYTHONW%".
  echo Create the virtual environment first with: uv venv .venv
  exit /b 1
)

if not exist "%AGENT%" (
  echo Missing "%AGENT%".
  exit /b 1
)

"%PYTHON%" "%AGENT%" --register-only
if errorlevel 1 exit /b %errorlevel%

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ProjectDir = $env:CLIPBOARD_RELAY_PROJECT_DIR; $TaskName = 'ClipboardRelayAgent'; $User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $Pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'; $Agent = Join-Path $ProjectDir 'agent.py'; $Action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('\"' + $Agent + '\"') -WorkingDirectory $ProjectDir; $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User; $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited; $Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0); $Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings; Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null"
if errorlevel 1 exit /b %errorlevel%

echo Installed scheduled task "%TASK_NAME%".
