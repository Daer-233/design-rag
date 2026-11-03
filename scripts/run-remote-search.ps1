param(
    [string]$BindAddress = '192.168.1.133',
    [int]$Port = 8030,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# This mounts app.remote_main only: /health, /ready and authenticated /api/v1/search.
# It deliberately does not expose ingestion, local files, PostgreSQL, Qdrant, or /api/v1/answer.
$args = @(
    '-n', 'design-rag',
    'python', '-m', 'uvicorn',
    'app.remote_main:app',
    '--app-dir', 'apps/backend',
    '--host', $BindAddress,
    '--port', $Port
)
if ($Reload) {
    $args += '--reload'
}
conda run @args
