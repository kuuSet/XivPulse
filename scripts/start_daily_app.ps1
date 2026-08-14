[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 180)]
    [int]$StartupTimeoutSeconds = 75,
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "backend"))
$startScript = Join-Path $projectRoot "start.ps1"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendIndex = Join-Path $projectRoot "frontend\dist\index.html"
$dataPath = Join-Path $projectRoot "data"
$logPortSuffix = if ($Port -eq 8765) { "" } else { ".$Port" }
$stdoutLog = Join-Path $dataPath "launcher.server${logPortSuffix}.stdout.log"
$stderrLog = Join-Path $dataPath "launcher.server${logPortSuffix}.stderr.log"
$appUrl = "http://127.0.0.1:$Port"
$healthUrl = "$appUrl/api/health"

function Get-ListenerProcessIds {
    try {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    catch [Microsoft.PowerShell.Cmdletization.Cim.CimJobException] {
        return @()
    }
}

function Get-ProcessRecord {
    param([int]$Id)

    return Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction SilentlyContinue
}

function Test-ProjectServerProcess {
    param([object]$ProcessRecord)

    if ($null -eq $ProcessRecord) {
        return $false
    }
    $name = [string]$ProcessRecord.Name
    $commandLine = [string]$ProcessRecord.CommandLine
    if ($name -notmatch "(?i)^pythonw?\.exe$" -or [string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }

    $escapedBackend = [Regex]::Escape($backendPath.TrimEnd("\"))
    $hasModule = [Regex]::IsMatch(
        $commandLine,
        "(?i)(?:^|\s)-m\s+uvicorn\s+app\.main:app(?:\s|$)"
    )
    $hasAppDirectory = [Regex]::IsMatch(
        $commandLine,
        "(?i)(?:^|\s)--app-dir(?:=|\s+)`"?$escapedBackend`"?(?:\s|$)"
    )
    $hasPort = [Regex]::IsMatch(
        $commandLine,
        "(?i)(?:^|\s)--port(?:=|\s+)$Port(?:\s|$)"
    )
    return $hasModule -and $hasAppDirectory -and $hasPort
}

function Test-ProjectHealth {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 3
        return $response.ok -eq $true -and [string]$response.status -eq "healthy"
    }
    catch {
        return $false
    }
}

function Assert-ListenersBelongToProject {
    param([int[]]$ProcessIds)

    foreach ($processId in $ProcessIds) {
        $record = Get-ProcessRecord -Id $processId
        if (-not (Test-ProjectServerProcess -ProcessRecord $record)) {
            $description = if ($record) {
                "$($record.Name) (PID $processId)"
            }
            else {
                "an unknown process (PID $processId)"
            }
            throw "Port $Port is already used by $description. The launcher will not start or stop that process."
        }
    }
}

function Open-Application {
    if (-not $NoBrowser) {
        Start-Process $appUrl | Out-Null
    }
}

$hashProvider = [Security.Cryptography.SHA256]::Create()
try {
    $pathBytes = [Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())
    $pathHash = [BitConverter]::ToString($hashProvider.ComputeHash($pathBytes)).Replace("-", "")
}
finally {
    $hashProvider.Dispose()
}
$mutex = [Threading.Mutex]::new($false, "Local\XivPulseLauncher-$($pathHash.Substring(0, 16))")
$hasMutex = $false

try {
    try {
        $hasMutex = $mutex.WaitOne([TimeSpan]::FromSeconds($StartupTimeoutSeconds))
    }
    catch [Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (-not $hasMutex) {
        throw "Another launcher is still waiting for XivPulse to start."
    }

    $listenerIds = @(Get-ListenerProcessIds)
    if ($listenerIds.Count -gt 0) {
        Assert-ListenersBelongToProject -ProcessIds $listenerIds
        $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Min(20, $StartupTimeoutSeconds))
        do {
            if (Test-ProjectHealth) {
                Write-Host "XivPulse is already running. Opening $appUrl"
                Open-Application
                exit 0
            }
            Start-Sleep -Milliseconds 500
        } while ([DateTime]::UtcNow -lt $deadline)
        throw "The XivPulse process owns port 8765 but its health check did not become ready."
    }

    foreach ($requiredPath in @($startScript, $pythonPath, $frontendIndex)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) {
            throw "The installed runtime is incomplete: $requiredPath is missing. Run start.ps1 once without skip options."
        }
    }
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null

    $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$startScript`"",
        "-SkipInstall",
        "-SkipFrontend",
        "-NoBrowser",
        "-HostAddress", "127.0.0.1",
        "-Port", "$Port"
    )
    $startParameters = @{
        FilePath               = $powerShell
        ArgumentList           = $arguments
        WorkingDirectory       = $projectRoot
        WindowStyle            = "Hidden"
        RedirectStandardOutput = $stdoutLog
        RedirectStandardError  = $stderrLog
        PassThru               = $true
    }
    $serverProcess = Start-Process @startParameters

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 500
        $listenerIds = @(Get-ListenerProcessIds)
        if ($listenerIds.Count -gt 0) {
            Assert-ListenersBelongToProject -ProcessIds $listenerIds
            if (Test-ProjectHealth) {
                Write-Host "XivPulse started successfully. Opening $appUrl"
                Open-Application
                exit 0
            }
        }
        if ($serverProcess.HasExited) {
            $details = ""
            if (Test-Path -LiteralPath $stderrLog) {
                $details = (Get-Content -LiteralPath $stderrLog -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            }
            throw "XivPulse exited before becoming ready. See $stderrLog.$([Environment]::NewLine)$details"
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "XivPulse did not become ready within $StartupTimeoutSeconds seconds. See $stderrLog."
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
