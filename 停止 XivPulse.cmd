@echo off
setlocal
echo This stops only the XivPulse web service owned by this project on port 8765.
echo A separate Windows scheduled update, if currently running, is not stopped.
choice /C YN /N /M "Continue? [Y/N] "
if errorlevel 2 exit /b 0
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_daily_app.ps1"
set "stop_exit=%errorlevel%"
echo.
if not "%stop_exit%"=="0" echo Stop failed. No unverified process was terminated.
pause
exit /b %stop_exit%
