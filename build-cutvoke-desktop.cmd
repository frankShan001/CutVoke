@echo off
setlocal EnableExtensions
set "ELECTRON_SKIP_BINARY_DOWNLOAD=1"
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 goto :failed
if not exist ".venv-desktop-build\Scripts\python.exe" (
  uv venv .venv-desktop-build --python 3.12
  if errorlevel 1 goto :failed
)
uv pip install --python .venv-desktop-build\Scripts\python.exe pyinstaller==6.22.3 cryptography==50.0.2 fontTools==4.66.1
if errorlevel 1 goto :failed
pushd web\app
call npm ci
if errorlevel 1 goto :popfailed
call npm run build
if errorlevel 1 goto :popfailed
popd
".venv-desktop-build\Scripts\python.exe" -m PyInstaller --noconfirm --distpath build\desktop-engine --workpath build\desktop-work desktop\engine.spec
if errorlevel 1 goto :failed
pushd desktop
call npm ci
if errorlevel 1 goto :popfailed
popd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\prepare_desktop_tools.ps1
if errorlevel 1 goto :failed
set "ELECTRON_BUILDER_NSIS_DIR=%~dp0build\desktop-downloads\nsis"
set "ELECTRON_BUILDER_NSIS_RESOURCES_DIR=%~dp0build\desktop-downloads\nsis-resources"
set "ELECTRON_BUILDER_7ZIP_PATH=%~dp0build\desktop-downloads\7zip\bin\7za.exe"
pushd desktop
call npm run dist
if errorlevel 1 goto :popfailed
popd
echo Installer is ready in dist\desktop.
exit /b 0
:popfailed
popd
:failed
echo Build failed. Check the output above. Requires Node.js, uv, FFmpeg and FFprobe.
exit /b 1
