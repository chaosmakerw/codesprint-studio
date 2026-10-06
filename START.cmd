@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 run.py --open
) else (
  python run.py --open
)
if errorlevel 1 (
  echo Startup failed. Install Python 3.12 or newer and check the error above.
  pause
)
endlocal
