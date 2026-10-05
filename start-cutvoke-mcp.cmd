@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONPATH=%CD%\src"
if not exist "%CD%\.venv\Scripts\python.exe" (
    echo CutVoke environment is missing. Run start-cutvoke.cmd once first. 1>&2
    exit /b 1
)
rem stdout is exclusively MCP JSON-RPC; startup diagnostics go to stderr.
"%CD%\.venv\Scripts\python.exe" -m cutvoke mcp %*
exit /b %errorlevel%
