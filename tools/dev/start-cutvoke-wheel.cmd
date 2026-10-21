@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 goto :failed
set "CUTVOKE_RELEASE=%CD%"
set "CUTVOKE_WHEEL="
for %%W in ("%CUTVOKE_RELEASE%\cutvoke-*.whl") do (
    if defined CUTVOKE_WHEEL (
        echo Keep exactly one CutVoke wheel next to this launcher.
        goto :failed
    )
    set "CUTVOKE_WHEEL=%%~fW"
)
if not exist "%CUTVOKE_WHEEL%" (
    echo Copy this launcher next to the delivered CutVoke wheel.
    goto :failed
)
set "PYTHONPATH="
if not exist "%CUTVOKE_RELEASE%\.venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv "%CUTVOKE_RELEASE%\.venv"
    ) else (
        python -m venv "%CUTVOKE_RELEASE%\.venv"
    )
    if errorlevel 1 goto :failed
)
set "CUTVOKE_PYTHON=%CUTVOKE_RELEASE%\.venv\Scripts\python.exe"
set "CUTVOKE_MARKER=%CUTVOKE_RELEASE%\.venv\cutvoke-wheel.sha256"
"%CUTVOKE_PYTHON%" -c "import cryptography,fontTools,hashlib,pathlib,sys; w=pathlib.Path(sys.argv[1]); m=pathlib.Path(sys.argv[2]); sys.exit(0 if m.is_file() and m.read_text().strip()==hashlib.sha256(w.read_bytes()).hexdigest() else 1)" "%CUTVOKE_WHEEL%" "%CUTVOKE_MARKER%" >nul 2>&1
if errorlevel 1 (
    "%CUTVOKE_PYTHON%" -m pip install --upgrade --force-reinstall "%CUTVOKE_WHEEL%"
    if errorlevel 1 goto :failed
    "%CUTVOKE_PYTHON%" -c "import hashlib,pathlib,sys; pathlib.Path(sys.argv[2]).write_text(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())" "%CUTVOKE_WHEEL%" "%CUTVOKE_MARKER%"
    if errorlevel 1 goto :failed
)
if not defined CUTVOKE_PORT set "CUTVOKE_PORT=8787"
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
