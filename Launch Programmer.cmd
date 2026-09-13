@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 programmer\app.py
) else (
  python programmer\app.py
)
if errorlevel 1 pause
