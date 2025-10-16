# FxLabs Server: Windows Orchestrator
# Provisions venv, installs requirements, starts FxLabs server, and runs Cloudflared.
# Usage examples:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\fxlabs-start.ps1
#   .\fxlabs-start.ps1 -ForceInstall -LaunchMT5

param(
    [switch]$ForceInstall = $false,
    [switch]$NoCloudflared = $false,
    [string]$EnvFile = ".env",
    [string]$CloudflaredConfig = "config.yml",
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

# Ensure the working directory is the script's directory (helps double-click scenarios)
try {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ($scriptRoot -and (Test-Path $scriptRoot)) { Set-Location -LiteralPath $scriptRoot }
} catch {}

# --- Branding colors (avoid black; use #19235d) ---
$ESC = [char]27
$BRAND = "$ESC[38;2;25;35;93m"   # #19235d -> rgb(25,35,93)
$RESET = "$ESC[0m"
function Write-Brand([string]$msg) { Write-Host "$BRAND$msg$RESET" }
function Write-Ok([string]$msg)    { Write-Host $msg -ForegroundColor Green }
function Write-Info([string]$msg)  { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn([string]$msg)  { Write-Host $msg -ForegroundColor Yellow }
function Write-Err([string]$msg)   { Write-Host $msg -ForegroundColor Red }

Write-Brand '================ FxLabs Server - Windows Runner ================'

# --- Ensure logs directory exists ---
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Force -Path "logs" | Out-Null }

# --- Locate Python ---
$pyCmd = $null
try {
    $null = & py -$PythonVersion -c "import sys;print(sys.version)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyCmd = "py -$PythonVersion" }
} catch {}
if (-not $pyCmd) {
    try {
        $ver = & python --version 2>&1
        if ($LASTEXITCODE -eq 0) { $pyCmd = "python" }
    } catch {}
}
if (-not $pyCmd) {
    Write-Err "Python $PythonVersion+ not found. Install Python and retry."
    exit 1
}
Write-Info "Python selected: $pyCmd"

# --- Create or reuse venv ---
$venvCreated = $false
if (-not (Test-Path ".venv")) {
Write-Info 'Creating virtual environment (.venv)...'
    try {
        & py -$PythonVersion -m venv .venv
    } catch {
        Write-Warn "py launcher failed; falling back to 'python -m venv'"
        & python -m venv .venv
    }
    if (-not (Test-Path ".venv")) {
        Write-Err 'Failed to create virtual environment.'
        exit 1
    }
    $venvCreated = $true
}
Write-Info 'Activating virtual environment...'
& .\.venv\Scripts\Activate.ps1

# --- Install/upgrade requirements if needed ---
if ($venvCreated -or $ForceInstall) {
    Write-Info 'Upgrading pip and installing requirements...'
    & python -m pip install --upgrade pip
    & pip install -r requirements.txt
}

# --- Load environment file (.env) if present ---
# Resolve relative to script folder if path is not absolute
if (-not [System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile = Join-Path -Path (Get-Location) -ChildPath $EnvFile }
if (Test-Path $EnvFile) {
    Write-Info ("Loading environment from '{0}'..." -f $EnvFile)
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^[\s]*#') { return }
        if ($_ -match '^[\s]*$') { return }
        if ($_ -match '^(?<k>[^=\s]+)\s*=\s*(?<v>.*)$') {
            $k = $matches['k'].Trim()
            $v = $matches['v'].Trim()
            # Trim matching wrapping quotes only without fragile quoting
            $DQ = [char]34  # double-quote char
            $SQ = [char]39  # single-quote char
            if ($v.StartsWith($DQ) -and $v.EndsWith($DQ)) {
                $v = $v.Trim($DQ)
            } elseif ($v.StartsWith($SQ) -and $v.EndsWith($SQ)) {
                $v = $v.Trim($SQ)
            }
            [Environment]::SetEnvironmentVariable($k, $v, 'Process')
        }
    }
} else {
    Write-Warn ("Env file '{0}' not found. Using current process env only." -f $EnvFile)
}

# MT5 is initialized by Python at runtime; no PS-level checks or launch.

# --- Start Cloudflared (background) unless disabled ---
$cloudProc = $null
if (-not $NoCloudflared) {
    # Resolve relative to script folder if path is not absolute
    if (-not [System.IO.Path]::IsPathRooted($CloudflaredConfig)) { $CloudflaredConfig = Join-Path -Path (Get-Location) -ChildPath $CloudflaredConfig }
    if (-not (Test-Path $CloudflaredConfig)) {
        Write-Warn ("Cloudflared config '{0}' not found. Skipping tunnel." -f $CloudflaredConfig)
    } else {
        $cfCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
        if (-not $cfCmd) {
            Write-Warn "cloudflared not found in PATH. Install Cloudflared to enable the tunnel."
        } else {
            # Validate credentials-file path if present in config
            try {
                $credLine = Select-String -Path $CloudflaredConfig -Pattern '^\s*credentials-file:\s*(.+)$' -CaseSensitive | Select-Object -First 1
                if ($credLine) {
                    $credPath = $credLine.Matches.Groups[1].Value.Trim()
                    # Expand ~ and env vars if any
                    $credPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($credPath)
                    if (-not (Test-Path $credPath)) {
                        Write-Warn ("Cloudflared credentials file not found: {0}" -f $credPath)
                    }
                }
            } catch {}
            Write-Info 'Starting Cloudflared tunnel in background...'
            if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Force -Path "logs" | Out-Null }
            $cloudArgs = @("tunnel","--config", $CloudflaredConfig, "run")
            $cloudProc = Start-Process -FilePath "cloudflared" -ArgumentList $cloudArgs -RedirectStandardOutput "logs/cloudflared.out.log" -RedirectStandardError "logs/cloudflared.err.log" -PassThru -WindowStyle Hidden
            if ($cloudProc) { Write-Ok ("Cloudflared started. PID={0}" -f $cloudProc.Id) } else { Write-Warn 'Failed to start Cloudflared.' }
        }
    }
}

# --- Run FxLabs server (foreground) ---
Write-Brand 'Starting FxLabs API (fxlabs-server.py) at http://127.0.0.1:8000'
Write-Info  'Health: http://127.0.0.1:8000/health'
try {
    & python fxlabs-server.py
    $serverExit = $LASTEXITCODE
} finally {
    if ($cloudProc -and -not $cloudProc.HasExited) {
        Write-Info ("Stopping Cloudflared (PID={0})..." -f $cloudProc.Id)
        try { Stop-Process -Id $cloudProc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
}

if ($serverExit -ne $null -and $serverExit -ne 0) {
    Write-Err ("FxLabs server exited with code {0}" -f $serverExit)
    exit $serverExit
}

Write-Ok 'FxLabs server stopped gracefully.'
