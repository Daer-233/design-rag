param([int]$CpuThreads = 8)
$ErrorActionPreference = 'Stop'
$knowledgeRoot = Split-Path -Parent $PSScriptRoot
$modelPython = Join-Path $env:USERPROFILE '.conda\envs\design-rag\python.exe'
if (-not (Test-Path -LiteralPath $modelPython)) { throw 'design-rag Python environment not found.' }
if ($CpuThreads -lt 1 -or $CpuThreads -gt 32) { throw 'CpuThreads must be between 1 and 32.' }
$modelLogs = Join-Path $knowledgeRoot 'data\manuals\runtime\shared-models'
New-Item -ItemType Directory -Force -Path $modelLogs | Out-Null
$priorOmp = $env:OMP_NUM_THREADS
$priorMkl = $env:MKL_NUM_THREADS
try {
    $env:OMP_NUM_THREADS = "$CpuThreads"
    $env:MKL_NUM_THREADS = "$CpuThreads"
    foreach ($model in @(
        @{ Name='embedding'; Port=8031; App='embedding_app.main:app'; Dir='apps/embedding'; Service='design-rag-embedding' },
        @{ Name='rerank'; Port=8032; App='rerank_app.main:app'; Dir='apps/rerank'; Service='design-rag-rerank' }
    )) {
        $listener = Get-NetTCPConnection -LocalPort $model.Port -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($model.Port)/health" -TimeoutSec 5
            if ($health.service -ne $model.Service) { throw "Port $($model.Port) belongs to an unexpected service." }
            Write-Output "Reusing shared $($model.Name) service on $($model.Port)."
            continue
        }
        # Eager model loading can precede the listening socket; reuse that process too.
        $modelPidFile = Join-Path $modelLogs "$($model.Name).pid"
        if (Test-Path -LiteralPath $modelPidFile) {
            $modelRecordedId = [int](Get-Content -LiteralPath $modelPidFile -Raw)
            $modelExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $modelRecordedId"
            if ($modelExisting) {
                if ($modelExisting.ExecutablePath -ine $modelPython -or
                    $modelExisting.CommandLine -notlike "*uvicorn $($model.App) *" -or
                    $modelExisting.CommandLine -notmatch "--port\s+$($model.Port)(?:\s|$)") {
                    throw "Recorded $($model.Name) PID belongs to another process."
                }
                Write-Output "Shared $($model.Name) process is already initializing on $($model.Port); reusing it."
                continue
            }
        }
        $arguments = @('-m','uvicorn',$model.App,'--app-dir',$model.Dir,'--host','127.0.0.1','--port',"$($model.Port)")
        $process = Start-Process -FilePath $modelPython -ArgumentList $arguments -WorkingDirectory $knowledgeRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $modelLogs "$($model.Name).out.log") -RedirectStandardError (Join-Path $modelLogs "$($model.Name).err.log")
        $process.Id | Set-Content -LiteralPath (Join-Path $modelLogs "$($model.Name).pid")
        Write-Output "Started shared $($model.Name) service on $($model.Port), CPU threads=$CpuThreads; model loading follows the existing configuration."
    }
} finally {
    if ($null -eq $priorOmp) { Remove-Item Env:OMP_NUM_THREADS -ErrorAction SilentlyContinue } else { $env:OMP_NUM_THREADS = $priorOmp }
    if ($null -eq $priorMkl) { Remove-Item Env:MKL_NUM_THREADS -ErrorAction SilentlyContinue } else { $env:MKL_NUM_THREADS = $priorMkl }
}
