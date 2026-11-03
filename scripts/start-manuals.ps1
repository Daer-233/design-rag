param([switch]$Initialize,[switch]$WithModels)
$ErrorActionPreference = 'Stop'
$manualRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $manualRoot
if ($WithModels) { & (Join-Path $PSScriptRoot 'start-knowledge-models.ps1') }
$manualPython = Join-Path $env:USERPROFILE '.conda\envs\design-rag\python.exe'
if (-not (Test-Path -LiteralPath $manualPython)) { throw 'Python 3.11 design-rag Conda environment not found.' }
if ($Initialize) { & $manualPython scripts/manual-instance.py init; if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' } }
$existingManual = Get-NetTCPConnection -LocalPort 8060 -State Listen -ErrorAction SilentlyContinue
if ($existingManual) { Write-Output 'Port 8060 already in use; check http://127.0.0.1:8060/knowledge/'; exit }
$manualLogs = Join-Path $manualRoot 'data\manuals\runtime'
New-Item -ItemType Directory -Force -Path $manualLogs | Out-Null
$manualPidFile = Join-Path $manualLogs 'server.pid'
if (Test-Path -LiteralPath $manualPidFile) {
    $manualRecordedId = [int](Get-Content -LiteralPath $manualPidFile -Raw)
    $manualExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $manualRecordedId"
    if ($manualExisting) {
        if ($manualExisting.ExecutablePath -ine $manualPython -or $manualExisting.CommandLine -notmatch 'manual-instance\.py["'']?\s+serve(?:\s|$)') {
            throw 'Recorded backend PID belongs to another process.'
        }
        Write-Output 'Knowledge backend is already initializing; reusing it.'
        exit
    }
}
$manualProcess = Start-Process -FilePath $manualPython -ArgumentList @('scripts/manual-instance.py','serve') -WorkingDirectory $manualRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $manualLogs 'server.out.log') -RedirectStandardError (Join-Path $manualLogs 'server.err.log')
$manualProcess.Id | Set-Content -LiteralPath (Join-Path $manualLogs 'server.pid')
Write-Output 'Work knowledge assistant starting: http://127.0.0.1:8060/knowledge/'
