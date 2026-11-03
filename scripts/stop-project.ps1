[CmdletBinding()]
param(
    [switch]$KeepInfrastructure
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$servicePorts = [int[]](8030, 8040, 8031, 8032)

function Get-ListeningProcessIds {
    param([Parameter(Mandatory)][int]$Port)

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return @($listeners | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
}

function Stop-ServiceOnPort {
    param([Parameter(Mandatory)][int]$Port)

    $processIds = @(Get-ListeningProcessIds -Port $Port)
    if ($processIds.Count -eq 0) {
        Write-Host "Port $Port is not in use."
        return
    }

    foreach ($processId in $processIds) {
        $processName = '<unknown>'
        try { $processName = (Get-Process -Id $processId -ErrorAction Stop).ProcessName } catch {}
        Write-Host "Stopping $processName (PID $processId) on port $Port..."
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if ((@(Get-ListeningProcessIds -Port $Port)).Count -eq 0) {
            Write-Host "Port $Port has been released."
            return
        }
        Start-Sleep -Milliseconds 400
    }

    throw "Port $Port is still occupied after the process stop request."
}

Set-Location $projectRoot
foreach ($port in $servicePorts) {
    Stop-ServiceOnPort -Port $port
}

if (-not $KeepInfrastructure) {
    Write-Host 'Stopping PostgreSQL and Qdrant containers for the design-rag project...'
    & (Join-Path $PSScriptRoot 'stop-infrastructure.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Infrastructure shutdown failed with exit code $LASTEXITCODE." }
}

Write-Host 'Design_RAG has been stopped.'
