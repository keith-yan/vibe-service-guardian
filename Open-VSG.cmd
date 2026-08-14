@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "VibeServiceGuardian.exe" (
  "VibeServiceGuardian.exe" --open-existing
  exit /b %errorlevel%
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m vsg --open-existing
  exit /b %errorlevel%
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python.exe -m vsg --open-existing
  exit /b %errorlevel%
)
py.exe -3 -m vsg --open-existing
exit /b %errorlevel%
