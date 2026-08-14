@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_daily_app.ps1"
set "launcher_exit=%errorlevel%"
if not "%launcher_exit%"=="0" (
  echo.
  echo XivPulse could not start. Read the message above or check data\launcher.server.stderr.log.
  pause
)
exit /b %launcher_exit%
