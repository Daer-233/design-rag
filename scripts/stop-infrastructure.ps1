param(
    [switch]$Volumes
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
. (Join-Path $PSScriptRoot 'docker-compose-helper.ps1')

if ($Volumes) {
    Invoke-DesignRagCompose -ComposeArguments @('down', '--volumes', '--timeout', '15') `
        -Operation 'Stopping Design_RAG PostgreSQL and Qdrant containers'
} else {
    Invoke-DesignRagCompose -ComposeArguments @('down', '--timeout', '15') `
        -Operation 'Stopping Design_RAG PostgreSQL and Qdrant containers'
}
