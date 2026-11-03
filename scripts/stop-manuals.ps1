$ErrorActionPreference = 'Stop'
$manualRoot = Split-Path -Parent $PSScriptRoot
$manualPidFile = Join-Path $manualRoot 'data\manuals\runtime\server.pid'
if (-not (Test-Path -LiteralPath $manualPidFile)) { Write-Output 'No manual server PID recorded'; exit }
$manualProcessId = [int](Get-Content -LiteralPath $manualPidFile -Raw)
$manualProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $manualProcessId"
if ($manualProcess -and $manualProcess.CommandLine -like '*manual-instance.py*serve*') {
    Stop-Process -Id $manualProcessId
    Remove-Item -LiteralPath $manualPidFile
    Write-Output 'Manual library stopped; shared database and model services remain running.'
} elseif ($manualProcess) { throw 'Recorded PID belongs to another process; refusing to stop it.' }
