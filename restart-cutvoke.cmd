@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_cutvoke.ps1"
if errorlevel 1 (
    echo.
    echo Restart did not complete. Read the error above.
    pause
    exit /b 1
)
echo.
echo Fully quit and reopen Codex to reconnect MCP, then return to this chat.
pause
exit /b 0
