[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "backend"))

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

$listenerIds = @(Get-ListenerProcessIds)
if ($listenerIds.Count -eq 0) {
    Write-Host "Nothing is listening on port $Port. XivPulse is already stopped."
    exit 0
}

$verified = @()
foreach ($processId in $listenerIds) {
    $record = Get-ProcessRecord -Id $processId
    if (-not (Test-ProjectServerProcess -ProcessRecord $record)) {
        $description = if ($record) {
            "$($record.Name) (PID $processId)"
        }
        else {
            "an unknown process (PID $processId)"
        }
        throw "Refusing to stop ${description}: it does not explicitly identify this project and port $Port."
    }
    $verified += $record
}

foreach ($record in $verified) {
    $processId = [int]$record.ProcessId
    $currentListenerIds = @(Get-ListenerProcessIds)
    $currentRecord = Get-ProcessRecord -Id $processId
    if ($processId -notin $currentListenerIds) {
        continue
    }
    if (-not (Test-ProjectServerProcess -ProcessRecord $currentRecord)) {
        throw "Process identity changed before stop. Nothing was stopped for PID $processId."
    }
    if ($PSCmdlet.ShouldProcess("XivPulse PID $processId on port $Port", "Stop local service")) {
        Stop-Process -Id $processId -ErrorAction Stop
    }
}

if ($WhatIfPreference) {
    exit 0
}

$deadline = [DateTime]::UtcNow.AddSeconds(8)
do {
    Start-Sleep -Milliseconds 250
    $remaining = @(Get-ListenerProcessIds)
} while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)

if ($remaining.Count -gt 0) {
    throw "Port $Port is still in use. No additional process was stopped."
}
Write-Host "XivPulse has stopped."
