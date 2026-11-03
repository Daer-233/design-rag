[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logsDirectory = Join-Path $projectRoot 'logs'
$servicePorts = [ordered]@{
    'Embedding' = 8031
    'Rerank' = 8032
    'Backend' = 8030
    'MCP' = 8040
}

function Test-PortListening {
    param([Parameter(Mandatory)][int]$Port)
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    ).Count -gt 0
}

function Wait-PortListening {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) {
            Write-Host "$Name is listening on port $Port."
            return
        }
        Start-Sleep -Seconds 1
    }

    throw "$Name did not start listening on port $Port within $TimeoutSeconds seconds. Check logs in $logsDirectory."
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "$Name is ready: $Url"
                return
            }
        } catch {}
        Start-Sleep -Seconds 1
    }

    throw "$Name did not become ready within $TimeoutSeconds seconds. Check logs in $logsDirectory."
}

function Start-BackgroundPowerShell {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$ScriptPath,
        [string[]]$ScriptArguments = @()
    )

    $stdout = Join-Path $logsDirectory ("{0}.stdout.log" -f $Name.ToLowerInvariant())
    $stderr = Join-Path $logsDirectory ("{0}.stderr.log" -f $Name.ToLowerInvariant())
    $powershellExe = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $powershellExe)) { $powershellExe = 'powershell.exe' }

    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $ScriptArguments
    $process = Start-Process -FilePath $powershellExe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-Host "$Name launcher started (PID $($process.Id)); logs: $stdout"
}

function Apply-MigrationsWithRetry {
    [int]$timeoutSeconds = 120
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++
        & (Join-Path $PSScriptRoot 'migrate.ps1') -Quiet
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Database migrations completed (attempt $attempt)."
            return
        }
        Write-Host "PostgreSQL is still initializing; retrying migrations (attempt $attempt)..."
        Start-Sleep -Seconds 3
    }

    throw "Database migrations could not complete within $timeoutSeconds seconds."
}

function Build-Frontends {
    Write-Host 'Building Chat web application...'
    & (Join-Path $PSScriptRoot 'build-chat-web.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Chat web build failed with exit code $LASTEXITCODE." }

    $adminRoot = Join-Path $projectRoot 'apps\admin-web'
    $bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $node = if ($nodeCommand) { $nodeCommand.Source } elseif (Test-Path -LiteralPath $bundledNode) { $bundledNode } else { throw 'Node.js was not found.' }
    $writerPython = Join-Path $env:USERPROFILE '.conda\envs\design-rag\python.exe'
    if (-not (Test-Path -LiteralPath $writerPython)) { $writerPython = 'python' }

    Write-Host 'Building Admin web application...'
    Push-Location $adminRoot
    try {
        $previousPython = $env:DESIGN_RAG_PYTHON
        $env:DESIGN_RAG_PYTHON = $writerPython
        & $node 'scripts\build-admin-safe.mjs'
        if ($LASTEXITCODE -ne 0) { throw "Admin web build failed with exit code $LASTEXITCODE." }
    } finally {
        if ($null -eq $previousPython) { Remove-Item Env:DESIGN_RAG_PYTHON -ErrorAction SilentlyContinue } else { $env:DESIGN_RAG_PYTHON = $previousPython }
        Pop-Location
    }
}

Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path $logsDirectory | Out-Null

if (-not $NoRestart) {
    Write-Host 'Stopping any prior Design_RAG services to prevent stale ports...'
    & (Join-Path $PSScriptRoot 'stop-project.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Prior project shutdown failed with exit code $LASTEXITCODE." }
}

Write-Host 'Starting PostgreSQL and Qdrant...'
& (Join-Path $PSScriptRoot 'run-infrastructure.ps1') -Detach
if ($LASTEXITCODE -ne 0) { throw "Infrastructure startup failed with exit code $LASTEXITCODE." }
Wait-PortListening -Name 'PostgreSQL' -Port 5432 -TimeoutSeconds 120
Wait-PortListening -Name 'Qdrant HTTP' -Port 6333 -TimeoutSeconds 120

Write-Host 'Applying database migrations...'
Apply-MigrationsWithRetry

if (-not $SkipFrontendBuild) {
    Build-Frontends
}

# conda run uses a shared temporary activation file on this Windows installation.
# Start these two Python services one after the other to avoid that file colliding.
Start-BackgroundPowerShell -Name 'Embedding' -ScriptPath (Join-Path $PSScriptRoot 'run-embedding.ps1')
Wait-PortListening -Name 'Embedding service' -Port $servicePorts['Embedding'] -TimeoutSeconds 300
Start-BackgroundPowerShell -Name 'Rerank' -ScriptPath (Join-Path $PSScriptRoot 'run-rerank.ps1')
Wait-PortListening -Name 'Rerank service' -Port $servicePorts['Rerank'] -TimeoutSeconds 300

Start-BackgroundPowerShell -Name 'Backend' -ScriptPath (Join-Path $PSScriptRoot 'run-backend.ps1') -ScriptArguments @('-Host', '0.0.0.0', '-Port', '8030')
Start-BackgroundPowerShell -Name 'MCP' -ScriptPath (Join-Path $PSScriptRoot 'run-mcp-server.ps1') -ScriptArguments @('-ListenHost', '0.0.0.0', '-Port', '8040')
Wait-HttpOk -Name 'Backend' -Url 'http://127.0.0.1:8030/health' -TimeoutSeconds 180
Wait-PortListening -Name 'MCP server' -Port $servicePorts['MCP'] -TimeoutSeconds 180
Wait-HttpOk -Name 'RAGAS evaluation API' -Url 'http://127.0.0.1:8030/api/v1/admin/rag-evaluations/capabilities' -TimeoutSeconds 60

Write-Host ''
Write-Host 'Design_RAG has started successfully.'
Write-Host 'Admin: http://127.0.0.1:8030/admin/'
Write-Host 'Chat:  http://127.0.0.1:8030/chat/'
Write-Host 'MCP:   http://127.0.0.1:8040/mcp'


