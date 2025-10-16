@echo off
REM FxLabs Server: Windows Orchestrator (BAT wrapper)
setlocal
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%fxlabs-start.ps1" %*
endlocal

