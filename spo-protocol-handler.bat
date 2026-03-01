@echo off
setlocal

REM Usage:
REM   spo-protocol-handler.bat dl "spodl://https://www.youtube.com/watch?v=..."
REM   spo-protocol-handler.bat tr "spotr://https://www.youtube.com/watch?v=..."

set "MODE=%~1"
set "RAW=%~2"

set "SCRIPT_DIR=%~dp0"
set "LOG_FILE=%SCRIPT_DIR%protocol-handler.log"

>>"%LOG_FILE%" echo.
>>"%LOG_FILE%" echo [%DATE% %TIME%] mode=%MODE% raw=%RAW%

if "%MODE%"=="" (
  echo ERROR: Missing mode (dl|tr)
  exit /b 1
)
if "%RAW%"=="" (
  echo ERROR: Missing URL argument
  exit /b 1
)

>>"%LOG_FILE%" echo [%DATE% %TIME%] step=after_args

set "URL=%RAW%"
>>"%LOG_FILE%" echo [%DATE% %TIME%] step=after_set_url

REM Strip protocol prefix if present
if /i "%MODE%"=="dl" (
  set "URL=%URL:spodl://=%"
  set "URL=%URL:spodl:=%"
) else (
  set "URL=%URL:spotr://=%"
  set "URL=%URL:spotr:=%"
)

>>"%LOG_FILE%" echo [%DATE% %TIME%] step=after_strip url=%URL%

REM Normalize the payload into a full YouTube URL
set "URL_PREFIX7=%URL:~0,7%"
set "URL_PREFIX8=%URL:~0,8%"

if /i "%URL_PREFIX7%"=="http://" (
  rem ok
) else if /i "%URL_PREFIX8%"=="https://" (
  rem ok
) else (
  rem No scheme: if it already looks like a YouTube host/path, prepend https://
  if /i not "%URL%"=="%URL:youtube.com=%" (
    set "URL=https://%URL%"
  ) else if /i not "%URL%"=="%URL:youtu.be=%" (
    set "URL=https://%URL%"
  ) else (
    rem Otherwise, assume it's just a video id
    set "URL=https://www.youtube.com/watch?v=%URL%"
  )
)

>>"%LOG_FILE%" echo [%DATE% %TIME%] step=after_normalize url=%URL%

>>"%LOG_FILE%" echo [%DATE% %TIME%] stripped_url=%URL%

if "%URL%"=="" (
  >>"%LOG_FILE%" echo [%DATE% %TIME%] ERROR empty_url_after_strip
  exit /b 1
)

REM Ensure we run from this repo folder
pushd "%SCRIPT_DIR%" >nul

REM Prefer Windows Terminal if available
set WT_CMD=
where wt >nul 2>nul && set WT_CMD=wt

if /i "%MODE%"=="dl" (
  set RUNNER=spo-dl-video.bat
  set TITLE=SPO Download
) else (
  set RUNNER=spo-translate-video.bat
  set TITLE=SPO Translate
)

>>"%LOG_FILE%" echo [%DATE% %TIME%] runner=%RUNNER% title=%TITLE% script_dir=%SCRIPT_DIR%

if not exist "%RUNNER%" (
  echo ERROR: Could not find %RUNNER% in %SCRIPT_DIR%
  >>"%LOG_FILE%" echo [%DATE% %TIME%] ERROR missing_runner=%RUNNER%
  popd >nul
  exit /b 1
)

if "%WT_CMD%"=="wt" (
  REM Open a new tab and keep it open so you can see the summary
  >>"%LOG_FILE%" echo [%DATE% %TIME%] launching=wt
  >>"%LOG_FILE%" echo [%DATE% %TIME%] cmdline=wt --title "%TITLE%" cmd /k ""%SCRIPT_DIR%%RUNNER%" "%URL%""
  start "" wt --title "%TITLE%" cmd /k ""%SCRIPT_DIR%%RUNNER%" "%URL%""
) else (
  REM Fallback: open a new cmd window
  >>"%LOG_FILE%" echo [%DATE% %TIME%] launching=cmd
  >>"%LOG_FILE%" echo [%DATE% %TIME%] cmdline=cmd /k ""%SCRIPT_DIR%%RUNNER%" "%URL%""
  start "%TITLE%" cmd /k ""%SCRIPT_DIR%%RUNNER%" "%URL%""
)

popd >nul
exit /b 0
