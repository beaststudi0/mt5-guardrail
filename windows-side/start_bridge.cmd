@echo off
rem Starts the MT5 bridge in this window. Leave the window open; Ctrl+C stops it.
rem Run it as a normal user - the bridge does not need Administrator rights.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bridge.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo The bridge stopped with exit code %RC%.
    pause
)
exit /b %RC%
