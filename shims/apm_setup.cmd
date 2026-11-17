@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
for %%I in ("%~dp0.") do set "SHIMDIR=%%~fI"
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

echo Generating shims\apm.exe ...
copy /y "%ROOT%\apps\apm\supporting\shims\kiennq\shim.exe" "%~dp0apm.exe" >nul
if errorlevel 1 exit /b 1
> "%~dp0apm.shim" (
    echo path = "%PY%"
    echo args = -X utf8 "%ROOT%\apps\apm\code\__main__.py"
)

rem Write shims dir to user PATH: bootstrap the apm package and reuse
rem apm.env.add_path (raw-value read, type preserved, dedup + prepend,
rem WM_SETTINGCHANGE broadcast). Idempotent; already-present entries are skipped.
"%PY%" -c "import importlib.util, os, sys; d = r'%ROOT%\apps\apm\code'; s = importlib.util.spec_from_file_location('apm', os.path.join(d, '__init__.py'), submodule_search_locations=[d]); m = importlib.util.module_from_spec(s); sys.modules['apm'] = m; s.loader.exec_module(m); from apm.env import add_path, read_path; b = read_path()[0]; add_path([r'%SHIMDIR%']); print(r'Already in user PATH: %SHIMDIR%' if b == read_path()[0] else r'Added to user PATH: %SHIMDIR%')"
if errorlevel 1 exit /b 1

echo.
echo Setup complete. APM entry directory: %SHIMDIR%
echo Already-running terminals need a restart to pick up the PATH change.
exit /b 0
