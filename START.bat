@echo off
REM DFY Sender: double click to start. First run sets everything up (2 to 3 minutes).
cd /d "%~dp0"
title DFY Sender

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

".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -e .
if errorlevel 1 ( echo Install failed. Send a screenshot of this window to Claude. & pause & exit /b 1 )

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo DFY Sender is starting. Your browser opens in a moment.
echo Keep this window open while sending. Close it to stop.
echo.
".venv\Scripts\engine.exe" app
pause
