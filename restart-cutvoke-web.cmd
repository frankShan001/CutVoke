@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_cutvoke.ps1" -WebOnly
if errorlevel 1 (
    echo.
    echo Web restart did not complete. Read the error above.
    pause
    exit /b 1
)
echo.
echo Web service is ready. Refresh the editor; Codex and MCP can stay open.
pause
exit /b 0
