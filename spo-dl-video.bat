@echo off
REM Download-only shortcut: same runner as spo-translate-video.bat, with --download-only forced.
set SCRIPT_DIR=%~dp0
call "%SCRIPT_DIR%spo-translate-video.bat" %* --download-only
exit /b %ERRORLEVEL%
