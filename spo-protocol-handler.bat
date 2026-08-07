@echo off
REM Invoked by Windows via the spodl:/spotr: URL protocol registry entries.
REM %1 = mode marker ("dl" or "tr"), %2 = raw URL/value after the ':'
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%spo-protocol-handler.ps1" -Mode %1 -Raw %2
