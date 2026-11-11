@echo off
setlocal
for %%I in ("%~dp0..") do set "APM_ROOT=%%~fI"
set "APM_PY=%APM_ROOT%\.venv\Scripts\python.exe"
if not exist "%APM_PY%" (
    echo [apm] Virtual environment not found. Run shims\apm_setup.cmd first.
    exit /b 1
)
set "PYTHONUTF8=1"
"%APM_PY%" "%APM_ROOT%\apps\apm\code\__main__.py" %*
exit /b %ERRORLEVEL%
