# MT5 Tick Stream Setup Script
# Run this script in PowerShell as Administrator if needed

Write-Host "Setting up MT5 Tick Stream Backend..." -ForegroundColor Green

# Check if Python is installed
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "Python not found. Please install Python 3.11+ first." -ForegroundColor Red
    exit 1
}

# Create virtual environment
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv") {
    Write-Host "Virtual environment already exists, removing..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".venv"
}

py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to create virtual environment. Trying with 'python'..." -ForegroundColor Yellow
    python -m venv .venv
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# Upgrade pip
Write-Host "Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install requirements
Write-Host "Installing requirements..." -ForegroundColor Yellow
pip install -r requirements.txt

if ($LASTEXITCODE -eq 0) {
    Write-Host "Setup completed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Copy config.env.example to .env and update the values" -ForegroundColor White
    Write-Host "2. Ensure MT5 is installed and running" -ForegroundColor White
    Write-Host "3. Run: python server.py" -ForegroundColor White
    Write-Host ""
    Write-Host "Or run the start script: .\start.ps1" -ForegroundColor White
} else {
    Write-Host "Setup failed. Please check the error messages above." -ForegroundColor Red
}
