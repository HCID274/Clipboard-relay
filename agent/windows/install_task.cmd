@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "PYTHONW=%PROJECT_DIR%\.venv\Scripts\pythonw.exe"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "AGENT=%PROJECT_DIR%\agent.py"
set "CONFIG_PATH=%PROJECT_DIR%\config.json"
set "TASK_NAME=ClipboardRelayAgent"
set "CLIPBOARD_RELAY_PROJECT_DIR=%PROJECT_DIR%"
set "CLIPBOARD_RELAY_CONFIG_PATH=%CONFIG_PATH%"

where uv >nul 2>&1
if errorlevel 1 (
  echo uv was not found in PATH. Install uv first from https://docs.astral.sh/uv/.
  exit /b 1
)

if not exist "%PYTHONW%" (
  echo Creating the Python virtual environment.
  uv venv "%VENV_DIR%"
  if errorlevel 1 exit /b %errorlevel%
)

if not exist "%AGENT%" (
  echo Missing "%AGENT%".
  exit /b 1
)

"%PYTHON%" -c "import pyperclip, websocket" >nul 2>&1
if errorlevel 1 (
  echo Installing Agent dependencies.
  uv pip install -r "%PROJECT_DIR%\requirements.txt" --python "%PYTHON%"
  if errorlevel 1 exit /b %errorlevel%
)

if not exist "%CONFIG_PATH%" (
  copy /y "%PROJECT_DIR%\config.example.json" "%CONFIG_PATH%" >nul
  if errorlevel 1 exit /b %errorlevel%
  echo Created "%CONFIG_PATH%" from config.example.json.
)

call :ensure_password
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" "%AGENT%" --register-only
if errorlevel 1 exit /b %errorlevel%

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ProjectDir = $env:CLIPBOARD_RELAY_PROJECT_DIR; $TaskName = 'ClipboardRelayAgent'; $User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name; $Pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'; $Agent = Join-Path $ProjectDir 'agent.py'; $Action = New-ScheduledTaskAction -Execute $Pythonw -Argument ('\"' + $Agent + '\"') -WorkingDirectory $ProjectDir; $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User; $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited; $Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0); $Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings; Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null"
if errorlevel 1 exit /b %errorlevel%

echo Installed scheduled task "%TASK_NAME%".
exit /b 0

:ensure_password
"%PYTHON%" -c "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import agent; sys.exit(0 if agent.config_needs_password(Path(sys.argv[2])) else 1)" "%PROJECT_DIR%" "%CONFIG_PATH%"
set "PASSWORD_STATUS=%ERRORLEVEL%"

if "%PASSWORD_STATUS%"=="1" exit /b 0
if not "%PASSWORD_STATUS%"=="0" (
  echo Cannot inspect the password in "%CONFIG_PATH%".
  exit /b %PASSWORD_STATUS%
)

:prompt_password
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ConfigPath = $env:CLIPBOARD_RELAY_CONFIG_PATH; $SecurePassword = Read-Host 'Enter the Clipboard Relay shared password' -AsSecureString; $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword); try { $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr) }; $Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json; $Config | Add-Member -NotePropertyName password -NotePropertyValue $Password -Force; $Json = ($Config | ConvertTo-Json -Depth 10) + [Environment]::NewLine; [IO.File]::WriteAllText($ConfigPath, $Json, [System.Text.UTF8Encoding]::new($false))"
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" -c "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import agent; sys.exit(0 if agent.config_needs_password(Path(sys.argv[2])) else 1)" "%PROJECT_DIR%" "%CONFIG_PATH%"
set "PASSWORD_STATUS=%ERRORLEVEL%"
if "%PASSWORD_STATUS%"=="1" exit /b 0
if "%PASSWORD_STATUS%"=="0" (
  echo The password must be non-placeholder ASCII text. Please try again.
  goto :prompt_password
)

echo Cannot inspect the password in "%CONFIG_PATH%".
exit /b %PASSWORD_STATUS%
