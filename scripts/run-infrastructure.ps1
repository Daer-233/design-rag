param(
    [switch]$Detach = $true
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
. (Join-Path $PSScriptRoot 'docker-compose-helper.ps1')

if ($Detach) {
    Invoke-DesignRagCompose -ComposeArguments @('up', '-d', 'postgres', 'qdrant') `
        -Operation 'Starting Design_RAG PostgreSQL and Qdrant containers'
} else {
    Invoke-DesignRagCompose -ComposeArguments @('up', 'postgres', 'qdrant') `
        -Operation 'Starting Design_RAG PostgreSQL and Qdrant containers' -TimeoutSeconds 300
}
