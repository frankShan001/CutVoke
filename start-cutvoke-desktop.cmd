@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "dist\desktop\win-unpacked\CutVoke.exe" (
  start "" "dist\desktop\win-unpacked\CutVoke.exe"
  exit /b 0
)
pushd desktop
if not exist "node_modules\electron\dist\electron.exe" (
  call npm ci
  if errorlevel 1 goto :failed
)
start "" "node_modules\electron\dist\electron.exe" .
popd
exit /b 0
:failed
popd
echo CutVoke desktop did not start. Check the output above.
pause
exit /b 1
