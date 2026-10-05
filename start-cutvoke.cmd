@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 goto :failed
set "CUTVOKE_ROOT=%CD%"
if not defined CUTVOKE_PORT set "CUTVOKE_PORT=8787"
set "PYTHONPATH=%CUTVOKE_ROOT%\src"

if not exist "%CUTVOKE_ROOT%\.venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv "%CUTVOKE_ROOT%\.venv"
    ) else (
        python -m venv "%CUTVOKE_ROOT%\.venv"
    )
    if errorlevel 1 goto :failed
    "%CUTVOKE_ROOT%\.venv\Scripts\python.exe" -m ensurepip --upgrade
    if errorlevel 1 goto :failed
    "%CUTVOKE_ROOT%\.venv\Scripts\python.exe" -m pip install -e "%CUTVOKE_ROOT%"
    if errorlevel 1 goto :failed
)
set "CUTVOKE_PYTHON=%CUTVOKE_ROOT%\.venv\Scripts\python.exe"
"%CUTVOKE_PYTHON%" -c "import cryptography, fontTools" >nul 2>&1
if errorlevel 1 (
    "%CUTVOKE_PYTHON%" -m ensurepip --upgrade
    if errorlevel 1 goto :failed
    "%CUTVOKE_PYTHON%" -m pip install -e "%CUTVOKE_ROOT%"
    if errorlevel 1 goto :failed
)

if not exist "%CUTVOKE_ROOT%\web\dist\index.html" (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo The Web editor is not built. Install Node.js 20 or newer.
        goto :failed
    )
    pushd "%CUTVOKE_ROOT%\web\app"
    if not exist "node_modules" (
        call npm ci
        if errorlevel 1 (
            popd
            goto :failed
        )
    )
    call npm run build
    if errorlevel 1 (
        popd
        goto :failed
    )
    popd
)
echo CutVoke: http://127.0.0.1:%CUTVOKE_PORT%
echo Keep this window open while using the editor. Press Ctrl+C to stop.
"%CUTVOKE_PYTHON%" -m cutvoke launch --port %CUTVOKE_PORT%
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo CutVoke did not start. Check the message above.
if not "%CUTVOKE_PAUSE_ON_ERROR%"=="0" pause
exit /b 1
