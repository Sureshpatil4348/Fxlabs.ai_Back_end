# MT5 Tick Stream Start Script
# Run this script to start the server

Write-Host "Starting MT5 Tick Stream Backend..." -ForegroundColor Green

# Check if virtual environment exists
if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Please run setup.ps1 first." -ForegroundColor Red
    exit 1
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# Check if .env file exists
if (-not (Test-Path ".env")) {
    Write-Host "Warning: .env file not found. Using default configuration." -ForegroundColor Yellow
    Write-Host "Copy config.env.example to .env and update values for production use." -ForegroundColor Yellow
}

# Load environment variables from .env if it exists
if (Test-Path ".env") {
    Write-Host "Loading environment variables from .env..." -ForegroundColor Yellow
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^([^#][^=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

# Check if MT5 is accessible
Write-Host "Checking MT5 connection..." -ForegroundColor Yellow
python -c "import MetaTrader5 as mt5; print('MT5 module imported successfully')"

if ($LASTEXITCODE -eq 0) {
    Write-Host "Starting server..." -ForegroundColor Green
    Write-Host "Server will be available at: http://127.0.0.1:8000" -ForegroundColor Cyan
    Write-Host "Health check: http://127.0.0.1:8000/health" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
    Write-Host ""
    
    # Start the server
    python server.py
} else {
    Write-Host "Failed to import MT5 module. Please ensure MT5 is installed and accessible." -ForegroundColor Red
    exit 1
}
