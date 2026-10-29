@echo off
setlocal enabledelayedexpansion

REM Extract + translate the NEW Q&A messages from sp.pedro.tokyo (see SPECIFICATIONS.md section 3.12).
REM Same venv as spo-translate-video.bat.
set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%" >nul

set PY_CMD=
where py >nul 2>nul && set PY_CMD=py
if "%PY_CMD%"=="" (
  where python >nul 2>nul && set PY_CMD=python
)

if not exist ".venv\Scripts\python.exe" (
  if "%PY_CMD%"=="" (
    echo ERROR: Python not found. Install Python 3.10+ and ensure it is on PATH.
    popd >nul
    pause
    exit /b 1
  )
  echo Creating virtual environment in .venv...
  %PY_CMD% -m venv .venv
  if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    popd >nul
    pause
    exit /b 1
  )
)

if not exist ".venv\deps_installed.marker" (
  echo Installing dependencies...
  call ".venv\Scripts\python.exe" -m pip install --upgrade pip
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    popd >nul
    pause
    exit /b 1
  )
  type nul > ".venv\deps_installed.marker"
)

REM Existing venvs were created before beautifulsoup4 was added to requirements.txt
call ".venv\Scripts\python.exe" -c "import bs4" >nul 2>nul
if errorlevel 1 (
  echo Installing beautifulsoup4...
  call ".venv\Scripts\python.exe" -m pip install "beautifulsoup4>=4.12"
)

call ".venv\Scripts\python.exe" pedro_shitsumons.py %*
set EXIT_CODE=%ERRORLEVEL%

popd >nul
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
