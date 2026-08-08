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
  pause
  exit /b 1
)

REM Create venv if missing
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv...
  %PY_CMD% -m venv .venv
  if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    popd >nul
    pause
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
    pause
    exit /b 1
  )

  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    popd >nul
    pause
    exit /b 1
  )

  type nul > ".venv\deps_installed.marker"
)

REM Periodically refresh yt-dlp: YouTube extraction breaks frequently, and yt-dlp ships fixes
REM very often (see SPECIFICATIONS.md section 8) — an install pinned once via the marker above
REM can silently fall many releases behind. Re-check for updates at most once every 7 days.
set YTDLP_MARKER=.venv\yt_dlp_last_update.marker
set NEEDS_YTDLP_UPDATE=1
if exist "%YTDLP_MARKER%" (
  for /f %%A in ('powershell -NoProfile -Command "if ((Get-Date) -lt (Get-Item '%YTDLP_MARKER%').LastWriteTime.AddDays(7)) { '0' } else { '1' }" 2^>nul') do set NEEDS_YTDLP_UPDATE=%%A
)
if "%NEEDS_YTDLP_UPDATE%"=="1" (
  echo Checking for yt-dlp updates ^(YouTube support changes frequently^)...
  call ".venv\Scripts\python.exe" -m pip install --upgrade yt-dlp >nul 2>nul
  type nul > "%YTDLP_MARKER%"
)

REM Run the app (pass all arguments through)
call ".venv\Scripts\python.exe" main.py %*
set EXIT_CODE=%ERRORLEVEL%

popd >nul
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
