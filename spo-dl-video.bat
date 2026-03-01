@echo off
setlocal enabledelayedexpansion

REM Always run from the folder where this .bat is located
set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%" >nul

REM Pick a Python launcher (prefer py if available)
set PY_CMD=
where py >nul 2>nul && set PY_CMD=py
if "%PY_CMD%"=="" (
  where python >nul 2>nul && set PY_CMD=python
)

if "%PY_CMD%"=="" (
  echo ERROR: Python not found. Install Python 3.10+ and ensure it is on PATH.
  popd >nul
  exit /b 1
)

REM Create venv if missing
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv...
  %PY_CMD% -m venv .venv
  if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    popd >nul
    exit /b 1
  )
)

REM Install dependencies once (marker file inside venv)
if not exist ".venv\deps_installed.marker" (
  echo Installing dependencies...
  call ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 (
    echo ERROR: Failed to upgrade pip.
    popd >nul
    exit /b 1
  )

  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    popd >nul
    exit /b 1
  )

  type nul > ".venv\deps_installed.marker"
)

REM Run the app in download-only mode by default (still pass all args through)
call ".venv\Scripts\python.exe" main.py --download-only %*
set EXIT_CODE=%ERRORLEVEL%

popd >nul
exit /b %EXIT_CODE%
