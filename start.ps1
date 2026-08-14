[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipFrontend,
    [switch]$NoBrowser,
    [string]$HostAddress = $(if ($env:XIVPULSE_HOST) { $env:XIVPULSE_HOST } elseif ($env:ARXIV_DAILY_HOST) { $env:ARXIV_DAILY_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:XIVPULSE_PORT) { [int]$env:XIVPULSE_PORT } elseif ($env:ARXIV_DAILY_PORT) { [int]$env:ARXIV_DAILY_PORT } else { 8765 })
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"
$frontendPath = Join-Path $projectRoot "frontend"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Creating the Python virtual environment..."
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    $pythonArguments = @("-3")
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        $pythonArguments = @()
    }
    if (-not $pythonCommand) {
        throw "Python was not found. Install Python 3.12 or newer and add it to PATH."
    }
    & $pythonCommand.Source @pythonArguments -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath)) {
        throw "Could not create the virtual environment. Check Python and folder permissions."
    }
}

if (-not $SkipInstall) {
    Write-Host "Installing backend dependencies..."
    & $pythonPath -m pip install --disable-pip-version-check -r (Join-Path $projectRoot "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
}

if (-not $SkipFrontend) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmCommand) {
        Write-Host "Building the frontend..."
        Push-Location $frontendPath
        try {
            if (Test-Path -LiteralPath (Join-Path $frontendPath "package-lock.json")) {
                & $npmCommand.Source ci
            }
            else {
                & $npmCommand.Source install
            }
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
            & $npmCommand.Source run build
            if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
        }
        finally {
            Pop-Location
        }
    }
    elseif (-not (Test-Path -LiteralPath (Join-Path $frontendPath "dist\index.html"))) {
        Write-Warning "Node.js/npm was not found and no frontend build exists. Only the API will be available."
    }
}

$env:PYTHONPATH = Join-Path $projectRoot "backend"
$url = "http://${HostAddress}:$Port"
Write-Host "Starting XivPulse at $url"

if (-not $NoBrowser -and $HostAddress -in @("127.0.0.1", "localhost")) {
    Start-Job -ScriptBlock {
        param($TargetUrl)
        Start-Sleep -Seconds 2
        Start-Process $TargetUrl
    } -ArgumentList $url | Out-Null
}

Push-Location $projectRoot
try {
    & $pythonPath -m uvicorn app.main:app --app-dir (Join-Path $projectRoot "backend") --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
