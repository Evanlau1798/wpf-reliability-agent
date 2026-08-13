param(
    [switch]$ResetCloud,
    [string]$ProjectId,
    [string]$ConfirmProjectId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runningDemo = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @("Demo.BrokenWpfApp.exe", "dotnet.exe") -and
    $_.CommandLine -match "Demo.BrokenWpfApp"
}
if ($runningDemo) {
    throw "Demo.BrokenWpfApp must be stopped before reset."
}

$outboxDirectory = Join-Path $env:LOCALAPPDATA "WpfReliabilityAgent\demo-broken-wpf-app"
foreach ($name in @("outbox.db", "outbox.db-wal", "outbox.db-shm")) {
    Remove-Item -LiteralPath (Join-Path $outboxDirectory $name) -Force -ErrorAction SilentlyContinue
}

if ($ResetCloud) {
    if ([string]::IsNullOrWhiteSpace($ProjectId) -or $ConfirmProjectId -cne $ProjectId) {
        throw "ConfirmProjectId must exactly match ProjectId for cloud reset."
    }
    $python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Repository-local Python environment is missing."
    }
    & $python (Join-Path $PSScriptRoot "..\src\cloud\scripts\reset_demo.py") --project $ProjectId
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud demo reset failed."
    }
}

Write-Host "Demo reset completed."
