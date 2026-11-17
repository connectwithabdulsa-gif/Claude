@echo off
REM DFY Sender: double click to start. First run sets everything up (2 to 3 minutes).
cd /d "%~dp0"
title DFY Sender
set "PYTHONPATH=%~dp0src"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up DFY Sender, this takes 2 to 3 minutes...
  python -m venv .venv
  if errorlevel 1 ( echo Could not create the .venv folder. & pause & exit /b 1 )
)

echo Checking the parts DFY Sender needs...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check flask typer pyyaml dnspython > install-log.txt 2>&1
if errorlevel 1 (
  echo.
  echo Install failed. The full error is in install-log.txt in this folder.
  echo Last lines:
  powershell -NoProfile -Command "Get-Content -Tail 15 'install-log.txt'"
  echo.
  echo Send a screenshot of this window to Claude.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo DFY Sender is starting. Your browser opens in a moment.
echo Keep this window open while sending. Close it to stop.
echo.
".venv\Scripts\python.exe" -m engine app
pause
