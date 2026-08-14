[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dataPath = Join-Path $projectRoot "data"
$logPath = Join-Path $dataPath "daily-task.log"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "The runtime is not installed. Run .\start.ps1 first."
}

$env:PYTHONPATH = Join-Path $projectRoot "backend"
New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
Push-Location $projectRoot
try {
    "[{0}] Daily task started." -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz") |
        Out-File -LiteralPath $logPath -Encoding utf8 -Append
    & $pythonPath (Join-Path $projectRoot "scripts\run_daily.py") 2>&1 |
        Tee-Object -FilePath $logPath -Append
    $exitCode = $LASTEXITCODE
    "[{0}] Daily task finished with exit code {1}." -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"), $exitCode |
        Out-File -LiteralPath $logPath -Encoding utf8 -Append
    exit $exitCode
}
catch {
    "[{0}] Daily task launcher failed: {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"), $_.Exception.Message |
        Out-File -LiteralPath $logPath -Encoding utf8 -Append
    throw
}
finally {
    Pop-Location
}
