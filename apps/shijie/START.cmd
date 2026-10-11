@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
chcp 65001 >nul
title Shijie - Learning from your project
py -3.12 -c "pass" >nul 2>&1
if not errorlevel 1 goto py312
py -3 -c "pass" >nul 2>&1
if not errorlevel 1 goto py3
python -c "pass" >nul 2>&1
if not errorlevel 1 goto python

echo Python 3.12 or later is required.
echo Install it from https://www.python.org/downloads/ and try again.
if not defined STUDY_NO_PAUSE pause
exit /b 1

:py312
py -3.12 run.py --open %*
goto finished
:py3
py -3 run.py --open %*
goto finished
:python
python run.py --open %*
:finished
set "STUDY_EXIT=%ERRORLEVEL%"
if "%STUDY_EXIT%"=="0" exit /b 0
echo Startup failed. See the message above.
echo If the port is busy, try START.cmd --port 8771
if not defined STUDY_NO_PAUSE pause
exit /b %STUDY_EXIT%
