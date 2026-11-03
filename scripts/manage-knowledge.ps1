param(
    [ValidateSet('Start','Restart')][string]$Action = 'Start',
    [ValidateRange(10,600)][int]$StartupTimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
$knowledgeRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $knowledgeRoot
$knowledgePython = Join-Path $env:USERPROFILE '.conda\envs\design-rag\python.exe'
$runtimeRoot = Join-Path $knowledgeRoot 'data\manuals\runtime'
$services = @(
    @{ Name='Knowledge backend'; Port=8060; Match='manual-instance\.py["'']?\s+serve(?:\s|$)'; PidFile=(Join-Path $runtimeRoot 'server.pid') },
    @{ Name='Embedding'; Port=8031; Match='uvicorn\s+embedding_app\.main:app\s'; PidFile=(Join-Path $runtimeRoot 'shared-models\embedding.pid') },
    @{ Name='Rerank'; Port=8032; Match='uvicorn\s+rerank_app\.main:app\s'; PidFile=(Join-Path $runtimeRoot 'shared-models\rerank.pid') }
)

function Get-ServiceListeners($Service) {
    @(Get-NetTCPConnection -State Listen -LocalPort $Service.Port -ErrorAction SilentlyContinue)
}

function Assert-ServiceProcess($Service, [int]$ProcessId) {
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
    if (-not $candidate) { return $null }
    if ($candidate.ExecutablePath -ine $knowledgePython -or $candidate.CommandLine -notmatch $Service.Match) {
        throw "Refusing to manage PID $ProcessId for $($Service.Name): process identity does not match."
    }
    if ($Service.Port -ne 8060 -and $candidate.CommandLine -notmatch "--port\s+$($Service.Port)(?:\s|$)") {
        throw "Refusing to manage PID $ProcessId`: model port does not match."
    }
    return $candidate
}

function Get-ServiceProcesses($Service, [switch]$IncludeRecorded) {
    $processIds = @(Get-ServiceListeners $Service | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($IncludeRecorded -and (Test-Path -LiteralPath $Service.PidFile)) {
        $recordedId = 0
        if (-not [int]::TryParse((Get-Content -LiteralPath $Service.PidFile -Raw).Trim(), [ref]$recordedId)) {
            throw "Invalid PID file: $($Service.PidFile)"
        }
        $processIds += $recordedId
    }
    foreach ($processIdValue in ($processIds | Select-Object -Unique)) {
        if ($processIdValue -le 0) { throw 'Invalid service PID.' }
        $candidate = Assert-ServiceProcess $Service $processIdValue
        if ($candidate) { $candidate }
    }
}

function Ensure-Storage {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is missing. Install/open Docker Desktop first.' }
    foreach ($storage in @(
        @{ Name='design-rag-postgres-1'; Service='postgres' },
        @{ Name='design-rag-qdrant-1'; Service='qdrant' }
    )) {
        # Inspect only public identity/state fields, never container environment secrets.
        # Avoid embedded quotes in Go templates: Windows PowerShell 5.1 strips them in native arguments.
        $identity = & docker inspect --format '{{json .Config.Labels}}' $storage.Name 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Cannot find $($storage.Name). Open Docker Desktop and check the design-rag project." }
        $labels = $identity | ConvertFrom-Json
        if ($labels.'com.docker.compose.project' -ne 'design-rag' -or $labels.'com.docker.compose.service' -ne $storage.Service) {
            throw "Unexpected storage container identity: $($storage.Name)"
        }
        $running = & docker inspect --format '{{.State.Running}}' $storage.Name 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect storage state: $($storage.Name)" }
        if (([string]$running).Trim() -ne 'true') {
            Write-Host "Starting existing storage container: $($storage.Name)"
            & docker start $storage.Name
            if ($LASTEXITCODE -ne 0) { throw "Storage startup failed: $($storage.Name)" }
        }
    }
    $deadline = (Get-Date).AddSeconds(60)
    do {
        $storageReady = $true
        foreach ($containerName in @('design-rag-postgres-1','design-rag-qdrant-1')) {
            $state = & docker inspect --format '{{ .State.Health.Status }}' $containerName 2>$null
            if ($LASTEXITCODE -ne 0 -or ([string]$state).Trim() -ne 'healthy') { $storageReady = $false }
        }
        if ($storageReady) { Write-Host 'PostgreSQL and Qdrant are healthy.'; return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'Storage did not become healthy within 60 seconds. Check Docker Desktop.'
}

function Wait-ServiceHealth($Service) {
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    do {
        $health = $null
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($Service.Port)/health" -TimeoutSec 3 } catch { }
        if ($health) {
            if ($Service.Port -eq 8060) {
                if ($health.status -ne 'ok' -or $health.library -ne 'manuals' -or $health.database -ne 'manual_rag' -or $health.collection -ne 'manual_rag_chunks_v1') {
                    throw 'Backend health identity mismatch; check the isolated knowledge configuration.'
                }
            } else {
                $expectedName = if ($Service.Port -eq 8031) { 'design-rag-embedding' } else { 'design-rag-rerank' }
                if ($health.service -ne $expectedName -or $health.status -notin @('ok','not_ready')) {
                    throw "Unexpected health response on port $($Service.Port)."
                }
            }
            $null = @(Get-ServiceProcesses $Service)
            $modelState = if ($health.status -eq 'not_ready') { ' (model loads on first query)' } else { '' }
            Write-Host "OK: $($Service.Name) on $($Service.Port)$modelState" -ForegroundColor Green
            return
        }
        $processes = @(Get-ServiceProcesses $Service -IncludeRecorded)
        if (-not $processes.Count) { throw "$($Service.Name) exited. See logs under $runtimeRoot" }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "$($Service.Name) startup timed out. See logs under $runtimeRoot"
}

$knowledgeMutex = New-Object System.Threading.Mutex($false, 'Local\DesignRagWorkKnowledge8060')
$hasLock = $false
$hasTranscript = $false
$exitCode = 0
try {
    try { $hasLock = $knowledgeMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $hasLock = $true }
    if (-not $hasLock) { throw 'Another knowledge startup/restart is in progress. Wait for that window to finish.' }
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    $launcherLog = Join-Path $runtimeRoot ('launcher-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
    $null = Start-Transcript -LiteralPath $launcherLog
    $hasTranscript = $true
    Write-Host "Work Knowledge Assistant - $Action"
    if (-not (Test-Path -LiteralPath $knowledgePython)) { throw 'The design-rag Conda Python environment is missing.' }
    if (-not (Test-Path -LiteralPath (Join-Path $knowledgeRoot 'instances\manuals\.env'))) { throw 'Knowledge instance configuration is missing. See docs/manual-library-v1.md.' }

    # Validate every target before stopping any service.
    $restartTargets = @{}
    foreach ($service in $services) {
        $restartTargets[$service.Port] = @(Get-ServiceProcesses $service -IncludeRecorded)
    }
    Ensure-Storage
    if ($Action -eq 'Restart') {
        Write-Host 'Restarting backend 8060 and shared models 8031/8032. Active queries may be interrupted.'
        foreach ($service in $services) {
            foreach ($target in $restartTargets[$service.Port]) {
                $current = Assert-ServiceProcess $service $target.ProcessId
                if ($current) {
                    if ($current.CreationDate -ne $target.CreationDate) { throw 'Service PID was reused; restart cancelled.' }
                    Stop-Process -Id $target.ProcessId -ErrorAction Stop
                    Write-Host "Stopped $($service.Name) (PID $($target.ProcessId))."
                }
            }
            $stopDeadline = (Get-Date).AddSeconds(15)
            while (@(Get-ServiceListeners $service).Count) {
                if ((Get-Date) -gt $stopDeadline) { throw "Port $($service.Port) is still occupied." }
                Start-Sleep -Milliseconds 300
            }
            if (Test-Path -LiteralPath $service.PidFile) { Remove-Item -LiteralPath $service.PidFile }
        }
    }

    & (Join-Path $PSScriptRoot 'start-knowledge-models.ps1')
    & (Join-Path $PSScriptRoot 'start-manuals.ps1')
    Write-Host 'Waiting for health checks. CPU model initialization can take several minutes.'
    foreach ($service in $services) { Wait-ServiceHealth $service }
    Write-Host ''
    Write-Host 'Open: http://127.0.0.1:8060/knowledge/' -ForegroundColor Cyan
    Write-Host 'Services run in the background. You can close this window.'
    Write-Host "Launcher log: $launcherLog"
} catch {
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Check service logs under: $runtimeRoot"
    $exitCode = 1
} finally {
    if ($hasTranscript) { $null = Stop-Transcript }
    if ($hasLock) { $knowledgeMutex.ReleaseMutex() }
    $knowledgeMutex.Dispose()
}
exit $exitCode
