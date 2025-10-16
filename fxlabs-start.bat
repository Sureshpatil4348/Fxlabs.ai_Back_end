@echo off
REM FxLabs Server: Windows Orchestrator (BAT wrapper)
setlocal

REM Ensure working directory is the script's folder
cd /d "%~dp0"

REM Run PowerShell script. On error, pause so the window doesn't close immediately.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\fxlabs-start.ps1" %*
if errorlevel 1 (
  echo.
  echo The script reported an error. Press any key to close...
  pause >nul
)

endlocal
