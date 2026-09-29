@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe "%~dp0A_close.py" %*
) else (
    python "%~dp0A_close.py" %*
)
set "exit_code=%errorlevel%"
endlocal & exit /b %exit_code%
