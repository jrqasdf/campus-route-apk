@echo off
chcp 65001 >nul
cd /d "%~dp0"
python windows\app.py
if errorlevel 1 pause
