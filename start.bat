@echo off
echo Starting MT5 Tick Stream Backend...
echo.

REM Check if virtual environment exists
if not exist ".venv" (
    echo Virtual environment not found. Please run setup.ps1 first.
    pause
    exit /b 1
)

REM Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Check if .env file exists
if not exist ".env" (
    echo Warning: .env file not found. Using default configuration.
    echo Copy config.env.example to .env and update values for production use.
    echo.
)

REM Load environment variables from .env if it exists
if exist ".env" (
    echo Loading environment variables from .env...
    for /f "tokens=1,2 delims==" %%a in (.env) do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" (
            set "%%a=%%b"
        )
    )
)

REM Check if MT5 is accessible
echo Checking MT5 connection...
python -c "import MetaTrader5 as mt5; print('MT5 module imported successfully')"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Starting server...
    echo Server will be available at: http://127.0.0.1:8000
    echo Health check: http://127.0.0.1:8000/health
    echo Press Ctrl+C to stop the server
    echo.
    
    REM Start the server
    python server.py
) else (
    echo.
    echo Failed to import MT5 module. Please ensure MT5 is installed and accessible.
    pause
    exit /b 1
)
