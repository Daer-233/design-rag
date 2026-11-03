$ErrorActionPreference = 'Stop'
$knowledgeRoot = Split-Path -Parent $PSScriptRoot
$daemonPidPath = Join-Path $knowledgeRoot 'data\manuals\runtime\langbot-codex\daemon.pid'
if (Test-Path -LiteralPath $daemonPidPath) {
    $daemonRecordedId = [int](Get-Content -LiteralPath $daemonPidPath -Raw)
    $daemonExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $daemonRecordedId"
    if ($daemonExisting -and $daemonExisting.CommandLine -like '*langbot-codex-daemon.py*') { Stop-Process -Id $daemonRecordedId }
    elseif ($daemonExisting) { throw 'Recorded daemon PID belongs to another process.' }
    Remove-Item -LiteralPath $daemonPidPath
}
& docker compose -f (Join-Path $knowledgeRoot 'instances\langbot-codex\compose.yaml') stop
Write-Output 'Codex Daemon and its relay stopped; LangBot and the knowledge service remain running.'
