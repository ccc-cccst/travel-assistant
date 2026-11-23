@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto fallback
py -3.12 start.py %*
goto finish
:fallback
python start.py %*
:finish
set start_status=%errorlevel%
if not "%start_status%"=="0" pause
exit /b %start_status%
