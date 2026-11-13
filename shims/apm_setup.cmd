@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "SYSPY="

where python >nul 2>nul
if errorlevel 1 goto try_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto try_py
set "SYSPY=python"
goto have_python

:try_py
where py >nul 2>nul
if errorlevel 1 goto no_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
set "SYSPY=py -3"
goto have_python

:no_python
echo Python 3.11 or newer was not found in PATH.
exit /b 1

:have_python
if not exist "%PY%" (
    echo Creating virtual environment .venv ...
    %SYSPY% -m venv "%VENV%"
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        exit /b 1
    )
)

echo Installing dependencies ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1

if not exist "%~dp0apm.exe" (
    echo Restoring shims\apm.exe ...
    copy /y "%ROOT%\apps\apm\supporting\shims\kiennq\shim.exe" "%~dp0apm.exe" >nul
    if errorlevel 1 exit /b 1
)
> "%~dp0apm.shim" (
    echo path = "%PY%"
    echo args = -X utf8 "%ROOT%\apps\apm\code\__main__.py"
)

echo.
echo Setup complete.
echo Add this directory to your user PATH:
echo        %~dp0
exit /b 0
