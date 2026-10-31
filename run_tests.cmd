@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m unittest discover -v
exit /b %ERRORLEVEL%
