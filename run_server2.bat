@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -u -m server --port 8000 > server_run5.log 2> server_run5_err.log
