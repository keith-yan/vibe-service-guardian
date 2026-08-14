@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "VibeServiceGuardian.exe" (
  "VibeServiceGuardian.exe" --stop
  exit /b %errorlevel%
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m vsg --stop
  exit /b %errorlevel%
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python.exe -m vsg --stop
  exit /b %errorlevel%
)
py.exe -3 -m vsg --stop
exit /b %errorlevel%
