@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: py -3.12 scripts\setup_prototype.py
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\run_prototype.py
if errorlevel 1 pause
