$ErrorActionPreference = 'Stop'
$knowledgeRoot = Split-Path -Parent $PSScriptRoot
$daemonRoot = Join-Path $knowledgeRoot 'data\manuals\runtime\langbot-codex'
$daemonPython = Join-Path $env:USERPROFILE '.conda\envs\design-rag\python.exe'
if (-not (Test-Path -LiteralPath $daemonPython)) { throw 'The design-rag Python environment is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $daemonRoot 'sdk\langbot_plugin\__init__.py'))) { throw 'Offline SDK is missing. See docs/langbot-work-knowledge.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $daemonRoot 'daemon.private.json'))) { throw 'Private daemon configuration is missing.' }
& docker compose -f (Join-Path $knowledgeRoot 'instances\langbot-codex\compose.yaml') up -d
if ($LASTEXITCODE -ne 0) { throw 'Codex loopback relay failed to start.' }
$daemonPidPath = Join-Path $daemonRoot 'daemon.pid'
if (Test-Path -LiteralPath $daemonPidPath) {
    $daemonRecordedId = [int](Get-Content -LiteralPath $daemonPidPath -Raw)
    $daemonExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $daemonRecordedId"
    if ($daemonExisting -and $daemonExisting.CommandLine -like '*langbot-codex-daemon.py*') { Write-Output 'Codex Daemon already running.'; exit }
    if ($daemonExisting) { throw 'Recorded daemon PID belongs to another process.' }
}
$daemonProcess = Start-Process -FilePath $daemonPython -ArgumentList @('-u', (Join-Path $knowledgeRoot 'scripts\langbot-codex-daemon.py')) -WorkingDirectory $knowledgeRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $daemonRoot 'daemon.out.log') -RedirectStandardError (Join-Path $daemonRoot 'daemon.err.log')
$daemonProcess.Id | Set-Content -LiteralPath $daemonPidPath
Write-Output 'Codex Daemon started; credentials remain in private local files.'
