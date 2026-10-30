@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m textlens %*
exit /b %ERRORLEVEL%
