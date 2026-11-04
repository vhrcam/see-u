@echo off
set "PYTHONSAFEPATH=1"
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m seeu %*
