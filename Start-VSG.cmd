@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "VibeServiceGuardian.exe" (
  powershell.exe -NoProfile -WindowStyle Hidden -Command "$p=Join-Path (Get-Location) 'VibeServiceGuardian.exe'; Start-Process -FilePath $p -ArgumentList '--open' -WorkingDirectory (Get-Location) -WindowStyle Hidden"
  exit /b %errorlevel%
)

if exist ".venv\Scripts\python.exe" (
  powershell.exe -NoProfile -WindowStyle Hidden -Command "$p=Join-Path (Get-Location) '.venv\Scripts\python.exe'; Start-Process -FilePath $p -ArgumentList @('-m','vsg','--open') -WorkingDirectory (Get-Location) -WindowStyle Hidden"
  exit /b %errorlevel%
)

where python.exe >nul 2>nul
if not errorlevel 1 (
  powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'python.exe' -ArgumentList @('-m','vsg','--open') -WorkingDirectory (Get-Location) -WindowStyle Hidden"
  exit /b %errorlevel%
)

where py.exe >nul 2>nul
if not errorlevel 1 (
  powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'py.exe' -ArgumentList @('-3','-m','vsg','--open') -WorkingDirectory (Get-Location) -WindowStyle Hidden"
  exit /b %errorlevel%
)

echo Vibe Service Guardian runtime was not found.
echo Use the portable package or install Python 3.10 or newer.
pause
exit /b 2
