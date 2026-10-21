@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 exit /b 1
if /i "%~1"=="--web" goto :web
if /i "%~1"=="--mcp" goto :mcp
if /i "%~1"=="--help" goto :help
if not "%~1"=="" goto :help
call tools\dev\start-cutvoke-web.cmd --prepare
if errorlevel 1 exit /b 1
call tools\dev\start-cutvoke-desktop.cmd
exit /b %errorlevel%
:web
call tools\dev\start-cutvoke-web.cmd
exit /b %errorlevel%
:mcp
call tools\dev\start-cutvoke-mcp.cmd
exit /b %errorlevel%
:help
 echo Usage: start-cutvoke.cmd [--web ^| --mcp ^| --help]
 echo Default: desktop editor. --web: browser editor. --mcp: agent connection.
exit /b 0
