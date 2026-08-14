[CmdletBinding()]
param(
    [string]$TaskName = "XivPulse Daily Update",
    [string]$At = "13:30"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$runner = Join-Path $projectRoot "run_daily.ps1"
$time = [DateTime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".venv\Scripts\python.exe"))) {
    throw "The runtime is not installed. Run .\start.ps1 before installing the task."
}

$powerShell = (Get-Command powershell.exe).Source
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`""
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "XivPulse: fetch, analyze, and rank new arXiv papers" -Force | Out-Null
Write-Host "Scheduled task installed: $TaskName (daily at $At, start when available, battery allowed)."
