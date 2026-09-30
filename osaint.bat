@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title OsaintMaint - Public Data Console
color 0B

set "OSAINT_PYTHON="
where py >nul 2>&1
if not errorlevel 1 set "OSAINT_PYTHON=py -3"
if defined OSAINT_PYTHON goto :python_found

where python >nul 2>&1
if not errorlevel 1 set "OSAINT_PYTHON=python"
if defined OSAINT_PYTHON goto :python_found

echo.
echo [OsaintMaint] Python 3.11 or newer is required.
echo Install Python from https://www.python.org/downloads/ and run this file again.
exit /b 1

:python_found
if not exist ".venv\Scripts\python.exe" (
    echo [OsaintMaint] Creating local Python environment...
    %OSAINT_PYTHON% -m venv ".venv"
    if errorlevel 1 exit /b 1
)

echo [OsaintMaint] Checking local installation...
call ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -e "."
if errorlevel 1 (
    echo [OsaintMaint] Installation failed. Check your Python installation and connection.
    exit /b 1
)

call ".venv\Scripts\osaint.exe" %*
exit /b %errorlevel%
